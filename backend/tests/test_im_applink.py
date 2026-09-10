"""自家 H5 链接在飞书 / Lark / 钉钉推送中转为客户端打开协议。"""

from urllib.parse import parse_qs, quote
from unittest.mock import patch

from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.services.platform_notification_service import (
    PlatformNotificationService,
    feishu_client,
)
from app.utils import im_applink as applink


HOST = "https://app.example"


def _patch_host(monkeypatch, host: str = HOST):
    monkeypatch.setattr(applink.settings, "REVIEW_REPORT_HOST", host)
    monkeypatch.setattr(
        applink.settings, "REVIEW_SESSION_PAGE_URL", "/v2/business/weekly-insight"
    )


def test_wrap_sidebar_urls_by_platform(monkeypatch):
    _patch_host(monkeypatch)
    url = f"{HOST}/v2/behavior/rec-1"
    encoded = quote(url, safe="")

    assert (
        applink.wrap_first_party_url_for_im(url, PLATFORM_FEISHU)
        == f"https://applink.feishu.cn/client/web_url/open?mode=sidebar-semi&url={encoded}"
    )
    assert (
        applink.wrap_first_party_url_for_im(url, PLATFORM_LARK)
        == f"https://applink.larksuite.com/client/web_url/open?mode=sidebar-semi&url={encoded}"
    )
    assert (
        applink.wrap_first_party_url_for_im(url, PLATFORM_DINGTALK)
        == f"dingtalk://dingtalkclient/page/link?url={encoded}&pc_slide=true"
    )


def test_wrap_review_dashboard_uses_window(monkeypatch):
    _patch_host(monkeypatch)
    url = f"{HOST}/v2/business/weekly-insight?sessionId=abc&agent=evaluate"
    encoded = quote(url, safe="")

    feishu = applink.wrap_first_party_url_for_im(url, PLATFORM_FEISHU)
    assert feishu.startswith("https://applink.feishu.cn/client/web_url/open?mode=window&url=")
    assert encoded in feishu

    dingtalk = applink.wrap_first_party_url_for_im(url, PLATFORM_DINGTALK)
    assert dingtalk.startswith("dingtalk://dingtalkclient/page/link?url=")
    assert "pc_slide=false" in dingtalk
    assert encoded in dingtalk


def test_weekly_review_pages_use_window(monkeypatch):
    _patch_host(monkeypatch)
    review1 = f"{HOST}/v2/business/weekly-review/exec_1"
    review5 = f"{HOST}/v2/business/behavior-analysis/exec_5"
    assert applink.open_mode_for_url(review1) == "window"
    assert applink.open_mode_for_url(review5) == "window"
    assert "mode=window" in applink.wrap_first_party_url_for_im(review1, PLATFORM_FEISHU)


def test_does_not_wrap_external_or_non_http(monkeypatch):
    _patch_host(monkeypatch)
    external = "https://mi5p6bgsnf8.feishu.cn/minutes/obcn123"
    assert applink.wrap_first_party_url_for_im(external, PLATFORM_FEISHU) == external
    assert applink.wrap_first_party_url_for_im("8921437924", PLATFORM_DINGTALK) == "8921437924"
    assert applink.wrap_first_party_url_for_im("", PLATFORM_FEISHU) == ""


def test_does_not_double_wrap(monkeypatch):
    _patch_host(monkeypatch)
    url = f"{HOST}/v2/task/tid-1"
    wrapped = applink.wrap_first_party_url_for_im(url, PLATFORM_FEISHU)
    assert applink.wrap_first_party_url_for_im(wrapped, PLATFORM_FEISHU) == wrapped
    dingtalk = applink.wrap_first_party_url_for_im(url, PLATFORM_DINGTALK)
    assert applink.wrap_first_party_url_for_im(dingtalk, PLATFORM_DINGTALK) == dingtalk


def test_unknown_platform_keeps_https(monkeypatch):
    _patch_host(monkeypatch)
    url = f"{HOST}/v2/task/tid-1"
    assert applink.wrap_first_party_url_for_im(url, "slack") == url


def test_rewrite_markdown_keeps_label_and_hash(monkeypatch):
    _patch_host(monkeypatch)
    href = f"{HOST}/v2/behavior/rec-9#bottom"
    text = f"有人评论了你的拜访记录\n[附件.docx]({href})\n评论：你好"
    rewritten = applink.rewrite_first_party_urls_in_text(text, PLATFORM_FEISHU)
    assert rewritten.startswith("有人评论了你的拜访记录\n[附件.docx](https://applink.feishu.cn/")
    assert "mode=sidebar-semi" in rewritten
    assert quote(href, safe="") in rewritten
    assert f"]({href})" not in rewritten


def test_rewrite_exact_url_field_for_card_button(monkeypatch):
    _patch_host(monkeypatch)
    url = f"{HOST}/v2/behavior/rec-1/add-comment"
    rewritten = applink.rewrite_first_party_urls_in_text(url, PLATFORM_DINGTALK)
    assert rewritten.startswith("dingtalk://dingtalkclient/page/link?url=")
    assert "pc_slide=true" in rewritten
    query = parse_qs(rewritten.split("?", 1)[1])
    assert query["url"][0] == url


def test_rewrite_nested_card_vars_skips_external_visit_url(monkeypatch):
    _patch_host(monkeypatch)
    visit_url = "https://example.feishu.cn/docx/abc"
    original = {
        "type": "template",
        "data": {
            "template_id": "AAqtGR7vr9ezt",
            "template_variable": {
                "comment_page_url": f"{HOST}/v2/behavior/rec-1/add-comment",
                "visit_url": visit_url,
                "visit_url_md": f"[{visit_url}]({visit_url})",
                "sales_visit_records": [
                    {
                        "visit_url": visit_url,
                        "visit_url_md": f"[report.docx]({HOST}/v2/behavior/rec-1#bottom)",
                    }
                ],
            },
        },
    }
    rewritten = applink.rewrite_im_content_urls(original, PLATFORM_FEISHU)
    vars_ = rewritten["data"]["template_variable"]
    assert vars_["comment_page_url"].startswith("https://applink.feishu.cn/")
    assert vars_["visit_url"] == visit_url
    assert vars_["visit_url_md"] == f"[{visit_url}]({visit_url})"
    inner_md = vars_["sales_visit_records"][0]["visit_url_md"]
    assert inner_md.startswith("[report.docx](https://applink.feishu.cn/")
    assert original["data"]["template_variable"]["comment_page_url"].startswith(HOST)


def test_custom_review_session_path_uses_window(monkeypatch):
    _patch_host(monkeypatch)
    monkeypatch.setattr(applink.settings, "REVIEW_SESSION_PAGE_URL", "/custom/insight")
    url = f"{HOST}/custom/insight?sessionId=s1"
    assert applink.open_mode_for_url(url) == "window"
    assert "mode=window" in applink.wrap_first_party_url_for_im(url, PLATFORM_FEISHU)


def test_no_host_skips_wrap(monkeypatch):
    monkeypatch.setattr(applink.settings, "REVIEW_REPORT_HOST", "")
    url = "https://app.example/v2/behavior/rec-1"
    assert applink.wrap_first_party_url_for_im(url, PLATFORM_FEISHU) == url


def test_bare_url_inside_long_text_not_wrapped(monkeypatch):
    _patch_host(monkeypatch)
    notes = f"纪要里提到了 {HOST}/v2/behavior/rec-1 这个页面"
    assert applink.rewrite_first_party_urls_in_text(notes, PLATFORM_FEISHU) == notes


def test_dispatch_rewrites_content_without_mutating_original(monkeypatch):
    _patch_host(monkeypatch)
    original = {
        "type": "template",
        "data": {
            "template_id": "tpl",
            "template_variable": {
                "comment_page_url": f"{HOST}/v2/behavior/1/add-comment",
            },
        },
    }
    svc = PlatformNotificationService()
    with patch.object(feishu_client, "send_message", return_value={"ok": True}) as mock_send:
        svc._dispatch_platform_message(
            "ou_1",
            "token",
            original,
            PLATFORM_FEISHU,
            msg_type="interactive",
        )
    sent = mock_send.call_args.args[2]
    assert sent["data"]["template_variable"]["comment_page_url"].startswith(
        "https://applink.feishu.cn/client/web_url/open?mode=sidebar-semi&url="
    )
    assert (
        original["data"]["template_variable"]["comment_page_url"]
        == f"{HOST}/v2/behavior/1/add-comment"
    )

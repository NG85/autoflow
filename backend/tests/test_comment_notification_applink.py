"""评论推送：飞书卡片引用+按钮，钉钉 markdown，链接走侧栏协议。"""

from unittest.mock import MagicMock, patch
from urllib.parse import quote, unquote
from uuid import uuid4

from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.services.platform_notification_service import PlatformNotificationService
from app.utils import im_applink as applink

HOST = "https://app.example"


def _patch_host(monkeypatch, host: str = HOST):
    monkeypatch.setattr(applink.settings, "REVIEW_REPORT_HOST", host)


def _profile(platform: str, open_id: str = "ou_test"):
    profile = MagicMock()
    profile.oauth_user.open_id = open_id
    profile.oauth_user.provider = platform
    return profile


def _comment_text(url: str) -> str:
    return f"有人评论了你的拜访记录\n[客户  商机]({url})\n评论：你好\n"


def test_layout_comment_style_message_quotes_and_buttons():
    jump = f"{HOST}/v2/behavior/rec-1"
    title, body, buttons = PlatformNotificationService._layout_comment_style_message(
        f"张三回复了你的评论（10-23 16:04 编辑过）\n[客户]({jump})\n周总结（2026-10-20~2026-10-26）\n回复：你好\n"
    )
    assert title.startswith("张三回复了你的评论")
    assert "周总结（2026-10-20~2026-10-26）" in body
    assert body.endswith("> 你好")
    assert buttons == [("客户", jump)]
    reply_title, reply_body, _reply_buttons = PlatformNotificationService._layout_comment_style_message(
        "评论回复\n@乙在跟进记录 甲客户 中回复了你的评论\n评论：先问客户\n周五前给结论\n"
    )
    assert reply_title == "评论回复"
    assert reply_body == "@乙在跟进记录 甲客户 中回复了你的评论\n\n> 先问客户\n\n周五前给结论"


def test_visit_comment_feishu_uses_sidebar_button(monkeypatch):
    _patch_host(monkeypatch)
    service = PlatformNotificationService()
    jump = f"{HOST}/v2/behavior/rec-1"
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=_profile(PLATFORM_FEISHU),
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        result = service.send_visit_record_comment_notification(
            db,
            recipient_user_id=str(uuid4()),
            message_text=_comment_text(jump),
        )

    assert result["success"] is True
    args, kwargs = send_msg.call_args
    assert kwargs["msg_type"] == "interactive"
    card = args[2]
    assert card["header"]["title"]["content"] == "有人评论了你的拜访记录"
    assert card["header"]["template"] == "blue"
    assert card["body"]["elements"][0]["content"] == "> 你好"
    button = card["body"]["elements"][1]["columns"][0]["elements"][0]
    url = button["behaviors"][0]["default_url"]
    assert button["text"]["content"] == "客户  商机"
    assert url.startswith("https://applink.feishu.cn/client/web_url/open?mode=sidebar-semi&url=")
    assert quote(jump, safe="") in url


def test_weekly_comment_lark_uses_sidebar_button(monkeypatch):
    _patch_host(monkeypatch)
    service = PlatformNotificationService()
    jump = f"{HOST}/v2/weekly-followup?week_start=2026-10-20"
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=_profile(PLATFORM_LARK, "ou_lark"),
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        service.send_weekly_followup_comment_notification(
            db,
            recipient_user_id=str(uuid4()),
            message_text=_comment_text(jump).replace("拜访记录", "周跟进总结"),
        )

    card = send_msg.call_args.args[2]
    url = card["body"]["elements"][1]["columns"][0]["elements"][0]["behaviors"][0]["default_url"]
    assert "https://applink.larksuite.com/client/web_url/open?mode=sidebar-semi&url=" in url
    assert send_msg.call_args.kwargs["msg_type"] == "interactive"


def test_edited_comment_uses_orange_header(monkeypatch):
    _patch_host(monkeypatch)
    service = PlatformNotificationService()
    jump = f"{HOST}/v2/behavior/rec-1"
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=_profile(PLATFORM_FEISHU),
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        service.send_visit_record_comment_notification(
            db,
            recipient_user_id=str(uuid4()),
            message_text=f"有人评论了你的拜访记录（10-23 16:04 编辑过）\n[客户]({jump})\n评论：改了\n",
        )

    assert send_msg.call_args.args[2]["header"]["template"] == "orange"


def test_autoflow_visit_comment_card(monkeypatch):
    record_id = "form_20260918_054653_454_1f1b3245"
    host = "https://test.zhizhenzhihe.com"
    jump = f"{host}/v2/behavior/{record_id}"
    _patch_host(monkeypatch, host)
    service = PlatformNotificationService()
    message = (
        "张三评论了你的跟进记录\n"
        f"[成都康景生物科技有限公司  干细胞临床II期]({jump})\n"
        "评论：确认下周技术方案评审时间\n"
    )

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=_profile(PLATFORM_FEISHU),
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        service.send_visit_record_comment_notification(
            MagicMock(),
            recipient_user_id="019ab89d-ab72-7d69-b61e-1ede7450c0c3",
            message_text=message,
        )

    card = send_msg.call_args.args[2]
    assert card["header"]["title"]["content"] == "张三评论了你的跟进记录"
    assert card["body"]["elements"][0]["content"] == "> 确认下周技术方案评审时间"
    button = card["body"]["elements"][1]["columns"][0]["elements"][0]
    assert button["text"]["content"] == "成都康景生物科技有限公司  干细胞临床II期"
    url = button["behaviors"][0]["default_url"]
    assert url.startswith("https://applink.feishu.cn/client/web_url/open?mode=sidebar-semi&url=")
    assert quote(jump, safe="") in url


def test_today_highlight_comment_feishu_has_no_jump(monkeypatch):
    _patch_host(monkeypatch)
    service = PlatformNotificationService()
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=_profile(PLATFORM_FEISHU),
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        service.send_today_highlight_comment_notification(
            db,
            recipient_user_id=str(uuid4()),
            message_text=(
                "王经理给你留了评论\n"
                "甲客户  乙商机\n"
                "评论：先问客户周五前能不能定\n"
            ),
        )

    card = send_msg.call_args.args[2]
    assert card["header"]["title"]["content"] == "王经理给你留了评论"
    assert card["header"]["template"] == "blue"
    assert card["body"]["elements"][0]["content"] == "甲客户  乙商机\n\n> 先问客户周五前能不能定"
    assert len(card["body"]["elements"]) == 1
    assert "http" not in str(card)
    assert "applink" not in str(card)


def test_visit_comment_dingtalk_quotes_and_pc_slide(monkeypatch):
    _patch_host(monkeypatch)
    service = PlatformNotificationService()
    jump = f"{HOST}/v2/behavior/rec-9#bottom"
    db = MagicMock()

    with (
        patch(
            "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
            return_value=_profile(PLATFORM_DINGTALK, "ding_uid"),
        ),
        patch.object(service, "_get_tenant_access_token", return_value="token"),
        patch.object(service, "_send_message") as send_msg,
    ):
        service.send_visit_record_comment_notification(
            db,
            recipient_user_id=str(uuid4()),
            message_text=_comment_text(jump),
        )

    args, kwargs = send_msg.call_args
    assert kwargs["msg_type"] == "text"
    text = args[2]
    assert text.startswith("有人评论了你的拜访记录\n> 你好\n[客户  商机](dingtalk://dingtalkclient/page/link?url=")
    assert "pc_slide=true" in text
    assert unquote(text.split("url=", 1)[1].split("&", 1)[0]) == jump
    assert "评论：" not in text
    assert f"]({jump})" not in text

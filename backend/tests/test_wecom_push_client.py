"""企微经 Sia wecom/push 转发 bot markdown。"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.user_oauth_account import is_deliverable_oauth_account
from app.platforms.constants import PLATFORM_FEISHU, PLATFORM_WECOM
from app.platforms.wecom.push_client import WeComPushClient, WeComPushError
from app.services.platform_notification_service import PlatformNotificationService
from app.services.sales_today_highlights import deliver_highlight_card
from app.services.visit_record_recap_card import RecapLiteCard


def test_wecom_is_deliverable_oauth_provider():
    from app.models.user_oauth_account import oauth_push_receive_id

    account = MagicMock()
    account.provider = PLATFORM_WECOM
    account.open_id = None
    account.user_id_in_platform = "GaoNa"
    account.union_id = "GaoNa"
    assert oauth_push_receive_id(account) == "GaoNa"
    assert is_deliverable_oauth_account(account) is True


def test_wecom_push_not_configured_without_base_url(monkeypatch):
    monkeypatch.setattr(
        "app.platforms.wecom.push_client.settings.WECOM_PUSH_BASE_URL",
        "",
    )
    monkeypatch.setattr(
        "app.platforms.wecom.push_client.settings.WECOM_PUSH_TOKEN",
        "hub-token",
    )
    assert WeComPushClient().is_configured() is False


def test_wecom_send_markdown_posts_to_sia_hub(monkeypatch):
    monkeypatch.setattr(
        "app.platforms.wecom.push_client.settings.WECOM_PUSH_BASE_URL",
        "https://sia.example",
    )
    monkeypatch.setattr(
        "app.platforms.wecom.push_client.settings.WECOM_PUSH_TOKEN",
        "hub-token",
    )
    client = WeComPushClient()
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b'{"ok":true}'
    fake_resp.json.return_value = {"ok": True}
    with patch("app.platforms.wecom.push_client.requests.post", return_value=fake_resp) as post:
        result = client.send_markdown("ZhangSan", "# 标题\n正文")
    assert result == {"ok": True}
    args, kwargs = post.call_args
    assert args[0] == "https://sia.example/api/v1/webhook/wecom/push"
    assert kwargs["headers"]["Authorization"] == "Bearer hub-token"
    assert kwargs["json"] == {
        "chatid": "ZhangSan",
        "msgtype": "markdown",
        "markdown": {"content": "# 标题\n正文"},
    }


def test_wecom_send_markdown_omits_authorization_when_token_empty(monkeypatch):
    monkeypatch.setattr(
        "app.platforms.wecom.push_client.settings.WECOM_PUSH_BASE_URL",
        "https://sia.example",
    )
    monkeypatch.setattr(
        "app.platforms.wecom.push_client.settings.WECOM_PUSH_TOKEN",
        "",
    )
    client = WeComPushClient()
    assert client.is_configured() is True
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b'{"ok":true}'
    fake_resp.json.return_value = {"ok": True}
    with patch("app.platforms.wecom.push_client.requests.post", return_value=fake_resp) as post:
        client.send_markdown("ZhangSan", "hi")
    assert "Authorization" not in post.call_args.kwargs["headers"]


def test_wecom_send_message_rejects_interactive_cards():
    client = WeComPushClient()
    with pytest.raises(WeComPushError, match="interactive"):
        client.send_message(
            "ZhangSan",
            "token",
            {"type": "template", "data": {}},
            msg_type="interactive",
        )


def test_platform_service_validates_wecom_only_when_configured(monkeypatch):
    service = PlatformNotificationService()
    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.is_configured",
        lambda: False,
    )
    assert service._validate_platform_support(PLATFORM_WECOM) is False
    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.is_configured",
        lambda: True,
    )
    assert service._validate_platform_support(PLATFORM_WECOM) is True


def test_department_manager_from_profile_falls_back_to_wecom_userid(monkeypatch):
    service = PlatformNotificationService()
    oauth = MagicMock()
    oauth.provider = PLATFORM_WECOM
    oauth.open_id = None
    oauth.user_id_in_platform = "GaoNa"
    oauth.union_id = "GaoNa"
    profile = MagicMock()
    profile.oauth_user = oauth
    profile.name = "高娜"
    profile.direct_manager_name = None
    profile.user_id = uuid4()
    monkeypatch.setattr(
        "app.services.platform_notification_service.user_profile_repo.get_department_manager",
        lambda db, department_name: profile,
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.is_configured",
        lambda: True,
    )
    recipients = service.get_recipients_for_department_report_from_profile(
        MagicMock(), "华东一区"
    )
    assert len(recipients) == 1
    assert recipients[0]["open_id"] == "GaoNa"
    assert recipients[0]["platform"] == PLATFORM_WECOM
    assert recipients[0]["type"] == "department_manager"


def test_sales_daily_report_falls_back_to_wecom_userid(monkeypatch):
    service = PlatformNotificationService()
    oauth = MagicMock()
    oauth.provider = PLATFORM_WECOM
    oauth.open_id = None
    oauth.user_id_in_platform = "GaoNa"
    oauth.union_id = None
    profile = MagicMock()
    profile.user_id = uuid4()
    profile.name = "高娜"
    profile.department = "华东一区"
    profile.oauth_user = oauth
    monkeypatch.setattr(
        "app.services.platform_notification_service.user_profile_repo.get_by_recorder_id",
        lambda db, recorder_id: profile,
    )
    monkeypatch.setattr(
        service,
        "_user_has_receive_permission",
        lambda user_id, perm: True,
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.is_configured",
        lambda: True,
    )
    recipients = service.get_recipients_for_sales_daily_report(
        MagicMock(),
        recorder_id=str(profile.user_id),
        recorder_name="高娜",
    )
    assert len(recipients) == 1
    assert recipients[0]["open_id"] == "GaoNa"
    assert recipients[0]["platform"] == PLATFORM_WECOM


def test_send_messages_to_platform_skips_wecom_feishu_template():
    service = PlatformNotificationService()
    success, failed = service._send_messages_to_platform(
        PLATFORM_WECOM,
        [{"open_id": "GaoNa", "name": "高娜", "type": "recorder"}],
        {PLATFORM_WECOM: "token"},
        {"type": "template", "data": {"template_id": "AAqxxx", "template_variable": {}}},
    )
    assert success == 0
    assert len(failed) == 1
    assert "text_notice" in failed[0]["error"] or "interactive" in failed[0]["error"].lower() or "Feishu" in failed[0]["error"]


def test_department_daily_kpi_wecom_uses_basic_card(monkeypatch):
    from app.platforms.wecom.kpi_notice import build_kpi_report_wecom_basic_card

    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    card = build_kpi_report_wecom_basic_card(
        "department report",
        {
            "department_name": "华东一区",
            "report_date": "2026-11-10",
        },
    )
    assert card is not None
    assert card["main_title"]["title"].startswith("团队日报｜")
    assert card["jump_list"][0]["title"] == "查看报告"
    assert (
        card["jump_list"][0]["url"]
        == "https://example.com/v2/business/reports?tab=daily-team&date=2026-11-10"
    )
    assert "华东一区" in card["sub_title_text"]
    assert "horizontal_content_list" not in card


def test_company_weekly_kpi_wecom_uses_basic_card(monkeypatch):
    from app.platforms.wecom.kpi_notice import build_kpi_report_wecom_basic_card

    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    card = build_kpi_report_wecom_basic_card(
        "company weekly report",
        {"start_date": "2026-11-03", "end_date": "2026-11-09"},
    )
    assert card is not None
    assert "公司周报" in card["main_title"]["title"]
    assert "weekly-company" in card["card_action"]["url"]
    assert "周期：" in card["sub_title_text"]


def test_send_notifications_routes_wecom_kpi_to_basic_card(monkeypatch):
    service = PlatformNotificationService()
    sent = {}

    def fake_send(open_id, token, content, platform, receive_id_type="open_id", **kwargs):
        sent.update(
            {
                "open_id": open_id,
                "content": content,
                "msg_type": kwargs.get("msg_type"),
                "platform": platform,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.is_configured",
        lambda: True,
    )
    monkeypatch.setattr(service, "_get_platform_tokens", lambda platforms: {PLATFORM_WECOM: "t"})
    monkeypatch.setattr(service, "_send_message", fake_send)

    result = service._send_notifications_by_platform(
        recipients_by_platform={
            PLATFORM_WECOM: [
                {
                    "open_id": "GaoNa",
                    "name": "高娜",
                    "type": "department_manager",
                    "receive_id_type": "open_id",
                }
            ]
        },
        template_id_by_platform={PLATFORM_FEISHU: "AAqWNdowg4GU3"},
        template_vars={"department_name": "华东一区", "report_date": "2026-11-10"},
        notification_type="department report",
    )
    assert result["success"] is True
    assert sent["msg_type"] == "template_card"
    assert sent["content"]["card_type"] == "text_notice"
    assert sent["content"]["jump_list"][0]["title"] == "查看报告"


def test_visit_wecom_legacy_policy_uses_basic_card_without_recap(monkeypatch):
    from app.services.visit_record_push_policy import DEFAULT_VISIT_RECORD_PUSH_POLICY
    from app.services.visit_record_recap_card import RecapLiteCard

    monkeypatch.setattr(
        "app.services.visit_record_recap_card.build_visit_record_page_url",
        lambda record_id: f"https://app.example/v2/behavior/{record_id}",
    )
    service = PlatformNotificationService()
    lite = RecapLiteCard(
        title="正常 · 9月20日 · 星辰科技",
        header_template="blue",
        feishu_body="跟进人：李华",
        dingtalk_text="正文",
        feishu_summary="不应出现在精简卡",
        primary_action_links=(
            ("跟进详情", "https://example/x"),
            ("完整复盘报告", "https://example/recap"),
        ),
    )
    content, msg_type, error = service._visit_record_card_payload_for_recipient(
        "recorder",
        PLATFORM_WECOM,
        "form",
        None,
        {"recorder": "李华", "is_revised": False},
        DEFAULT_VISIT_RECORD_PUSH_POLICY,
        lite,
        record_id="rec-1",
        visit_record={
            "visit_communication_date": "2026-09-20",
            "followup_object_name": "星辰科技",
            "opportunity_name": "扩容",
            "recorder": "李华",
        },
    )
    assert error is None
    assert msg_type == "template_card"
    assert content["card_type"] == "text_notice"
    assert content["main_title"]["desc"] == "拜访记录"
    assert content["jump_list"] == [
        {
            "type": 1,
            "title": "跟进详情",
            "url": "https://app.example/v2/behavior/rec-1",
        }
    ]
    assert "不应出现在精简卡" not in (content.get("sub_title_text") or "")
    assert "跟进人：李华" in content["sub_title_text"]
    assert "horizontal_content_list" not in content


def test_visit_wecom_recap_lite_policy_keeps_full_card():
    from app.services.visit_record_push_policy import (
        CARD_RECAP_LITE,
        RolePushSpec,
        VisitRecordPushPolicy,
    )
    from app.services.visit_record_recap_card import RecapLiteCard

    policy = VisitRecordPushPolicy(
        recipients={
            "recorder": RolePushSpec(enabled=True, card=CARD_RECAP_LITE),
            "leader": RolePushSpec(enabled=True, card=CARD_RECAP_LITE),
            "collaborative_participant": RolePushSpec(enabled=True, card=CARD_RECAP_LITE),
            "configured_cc": RolePushSpec(enabled=True, card=CARD_RECAP_LITE),
        },
        groups={
            "review": RolePushSpec(enabled=False, card=CARD_RECAP_LITE),
            "brief": RolePushSpec(enabled=False, card=CARD_RECAP_LITE),
        },
    )
    service = PlatformNotificationService()
    lite = RecapLiteCard(
        title="正常 · 9月20日 · 星辰科技",
        header_template="blue",
        feishu_body="跟进人：李华",
        dingtalk_text="正文",
        feishu_summary="摘要",
        primary_action_links=(
            ("跟进详情", "https://example/x"),
            ("完整复盘报告", "https://example/recap"),
        ),
    )
    content, msg_type, error = service._visit_record_card_payload_for_recipient(
        "recorder",
        PLATFORM_WECOM,
        "form",
        None,
        {},
        policy,
        lite,
    )
    assert error is None
    assert msg_type == "template_card"
    assert content["jump_list"][0]["title"] == "完整复盘"
    assert "摘要" in content["sub_title_text"]


def test_resolve_department_recipients_keeps_oauth_list_even_if_open_id_empty(
    monkeypatch,
):
    """既有逻辑：OAuth 有名单即用，不因 open_id 空改走档案。"""
    service = PlatformNotificationService()
    oauth_recipients = [
        {
            "open_id": "",
            "name": "高娜",
            "platform": PLATFORM_WECOM,
            "type": "department_manager",
        }
    ]
    monkeypatch.setattr(
        "app.services.platform_notification_service.oauth_client.get_departments_with_leaders",
        lambda: {"华东一区": oauth_recipients},
    )
    monkeypatch.setattr(
        service,
        "get_recipients_for_department_report_from_profile",
        lambda db, department_name: [
            {
                "open_id": "GaoNa",
                "name": "高娜",
                "platform": PLATFORM_WECOM,
                "type": "department_manager",
            }
        ],
    )
    recipients = service.resolve_department_report_recipients(
        MagicMock(), "华东一区"
    )
    assert recipients == oauth_recipients


def test_department_report_skips_all_personal_when_any_review_group_exists(
    monkeypatch,
):
    """有 review 群则只打群：飞书/钉钉/企微同一规则，不按渠道补个人。"""
    service = PlatformNotificationService()
    personal_called = {"n": 0}
    group_calls = []

    monkeypatch.setattr(
        service,
        "_get_group_chats_by_department",
        lambda **kwargs: [
            {"chat_id": "oc_feishu", "name": "飞书群", "platform": "feishu"}
        ],
    )
    monkeypatch.setattr(service, "_validate_platform_support", lambda platform: True)

    def fake_send_groups(**kwargs):
        group_calls.append(kwargs.get("platform"))
        return 1 if kwargs.get("platform") == "feishu" else 0

    monkeypatch.setattr(service, "_send_card_to_group_chats", fake_send_groups)
    monkeypatch.setattr(
        service,
        "_send_notifications_by_platform",
        lambda **kwargs: personal_called.__setitem__("n", personal_called["n"] + 1)
        or {"success": True, "success_count": 0, "failed_recipients": []},
    )

    result = service._send_report_to_department_review_groups_or_recipients(
        MagicMock(),
        department_name="华东一区",
        recipients=[
            {
                "open_id": "ou_feishu",
                "name": "飞书负责人",
                "platform": "feishu",
                "type": "department_manager",
            },
            {
                "open_id": "GaoNa",
                "name": "高娜",
                "platform": PLATFORM_WECOM,
                "type": "department_manager",
                "receive_id_type": "open_id",
            },
        ],
        template_id_by_platform={"feishu": "tpl"},
        template_vars={"department_name": "华东一区", "report_date": "2026-11-10"},
        notification_type="department report",
    )
    assert result["success"] is True
    assert result["success_count"] == 1
    assert group_calls == ["feishu"]
    assert personal_called["n"] == 0


def test_department_report_sends_wecom_group_alongside_feishu_group(monkeypatch):
    service = PlatformNotificationService()
    group_calls = []
    personal_called = {"n": 0}

    monkeypatch.setattr(
        service,
        "_get_group_chats_by_department",
        lambda **kwargs: [
            {"chat_id": "oc_feishu", "name": "飞书群", "platform": "feishu"},
            {"chat_id": "wr_wecom", "name": "企微群", "platform": PLATFORM_WECOM},
        ],
    )
    monkeypatch.setattr(service, "_validate_platform_support", lambda platform: True)

    def fake_send_groups(**kwargs):
        group_calls.append(kwargs.get("platform"))
        return 1

    monkeypatch.setattr(service, "_send_card_to_group_chats", fake_send_groups)
    monkeypatch.setattr(
        service,
        "_send_notifications_by_platform",
        lambda **kwargs: personal_called.__setitem__("n", personal_called["n"] + 1)
        or {"success": True, "success_count": 0, "failed_recipients": []},
    )

    result = service._send_report_to_department_review_groups_or_recipients(
        MagicMock(),
        department_name="华东一区",
        recipients=[
            {
                "open_id": "GaoNa",
                "name": "高娜",
                "platform": PLATFORM_WECOM,
                "type": "department_manager",
            }
        ],
        template_id_by_platform={"feishu": "tpl"},
        template_vars={"department_name": "华东一区", "report_date": "2026-11-10"},
        notification_type="department report",
    )
    assert result["success"] is True
    assert result["success_count"] == 2
    assert set(group_calls) == {"feishu", PLATFORM_WECOM}
    assert personal_called["n"] == 0


def test_group_helpers_send_wecom(monkeypatch):
    service = PlatformNotificationService()
    groups = [{"chat_id": "wr_group_1", "name": "企微群", "platform": PLATFORM_WECOM}]
    sent = []

    def fake_send(open_id, token, content, platform, receive_id_type="open_id", **kwargs):
        sent.append(
            {
                "chatid": open_id,
                "content": content,
                "msg_type": kwargs.get("msg_type"),
                "receive_id_type": receive_id_type,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.is_configured",
        lambda: True,
    )
    monkeypatch.setattr(service, "_get_tenant_access_token", lambda platform: "t")
    monkeypatch.setattr(service, "_send_message", fake_send)
    monkeypatch.setattr(
        "app.utils.push_page_urls.settings.REVIEW_REPORT_HOST",
        "https://example.com",
    )

    assert (
        service._send_content_to_group_chats(
            PLATFORM_WECOM,
            groups,
            {"card_type": "text_notice", "main_title": {"title": "t", "desc": "d"}},
            "template_card",
        )
        == 1
    )
    assert service._send_text_to_group_chats(PLATFORM_WECOM, groups, "hello") == 1
    assert (
        service._send_card_to_group_chats(
            PLATFORM_WECOM,
            groups,
            "",
            {"department_name": "华东一区", "report_date": "2026-11-10"},
            notification_type="department report",
        )
        == 1
    )
    assert {item["chatid"] for item in sent} == {"wr_group_1"}
    assert any(item["msg_type"] == "template_card" for item in sent)
    assert any(item["msg_type"] == "text" for item in sent)
    kpi = next(
        item
        for item in sent
        if isinstance(item["content"], dict)
        and item["content"].get("card_type") == "text_notice"
        and "团队日报" in (item["content"].get("main_title") or {}).get("title", "")
    )
    assert kpi["receive_id_type"] == "chat_id"


def test_platform_service_dispatches_wecom_markdown(monkeypatch):
    service = PlatformNotificationService()
    sent = {}

    def fake_send(open_id, token, content, receive_id_type="open_id", **kwargs):
        sent.update(
            {
                "open_id": open_id,
                "token": token,
                "content": content,
                "msg_type": kwargs.get("msg_type"),
            }
        )
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.wecom_push_client.send_message",
        fake_send,
    )
    result = service._dispatch_platform_message(
        "ZhangSan",
        "hub-token",
        "hello **md**",
        PLATFORM_WECOM,
        msg_type="text",
    )
    assert result == {"ok": True}
    assert sent["open_id"] == "ZhangSan"
    assert sent["content"] == "hello **md**"
    assert sent["msg_type"] == "text"


def test_recap_lite_payload_for_wecom_uses_template_card():
    service = PlatformNotificationService()
    card = RecapLiteCard(
        title="正常 · 9月20日 · 星辰科技",
        header_template="blue",
        feishu_body="跟进人：李华",
        dingtalk_text="### 正常 · 9月20日 · 星辰科技\n正文",
        feishu_summary="摘要",
        feishu_action_links="[跟进详情](https://example/x)",
        primary_action_links=(
            ("跟进详情", "https://example/x"),
            ("完整复盘报告", "https://example/recap"),
        ),
        feishu_extract_actions=(("待办（2）", "https://example/todo"),),
    )
    content, msg_type = service._recap_lite_send_payload(PLATFORM_WECOM, card)
    assert msg_type == "template_card"
    assert content["card_type"] == "text_notice"
    assert content["jump_list"] == [
        {"type": 1, "title": "完整复盘", "url": "https://example/recap"},
        {"type": 1, "title": "跟进详情", "url": "https://example/x"},
    ]
    assert content["card_action"]["url"] == "https://example/recap"
    assert "摘要" in content["sub_title_text"]
    assert "[" not in content["sub_title_text"]
    horizontal = content.get("horizontal_content_list") or []
    assert all(row.get("type") == 1 and row.get("url") for row in horizontal)
    urls = {row["url"] for row in horizontal}
    assert "https://example/x" not in urls
    assert "https://example/todo" in urls


def test_deliver_sales_highlight_card_routes_wecom_to_template_card(monkeypatch):
    captured = {}

    def fake_send(open_id, token, content, platform, receive_id_type="open_id", **kwargs):
        captured.update(
            {
                "open_id": open_id,
                "content": content,
                "msg_type": kwargs.get("msg_type"),
                "platform": platform,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service"
        "._get_platform_tokens",
        lambda platforms: {PLATFORM_WECOM: "hub-token"},
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service"
        "._send_message",
        fake_send,
    )
    assembled = {
        "report_date": "2026-11-11",
        "has_content": True,
        "sections": [
            {
                "key": "immediate_today",
                "title": "立即处理",
                "url": "https://example.com/v2/today-highlights?date=2026-11-11&tab=immediate_today",
                "items": [
                    {
                        "claim": "先跟客户确认 POC",
                        "why_today": "周五要出结论",
                        "account_name": "星辰科技",
                    }
                ],
            }
        ],
        "card_links": [],
    }
    result = deliver_highlight_card(
        MagicMock(),
        {
            "user_id": str(uuid4()),
            "platform": PLATFORM_WECOM,
            "open_id": "GaoNa",
            "name": "高娜",
        },
        assembled,
    )
    assert result["success"] is True
    assert captured["open_id"] == "GaoNa"
    assert captured["msg_type"] == "template_card"
    card = captured["content"]
    assert card["card_type"] == "text_notice"
    assert card["main_title"]["title"].startswith("我的今日重点｜")
    assert 1 <= len(card["jump_list"]) <= 3
    assert "[查看]" not in (card.get("sub_title_text") or "")


def test_sales_wecom_card_groups_jump_and_horizontal(monkeypatch):
    from app.services.today_highlights_wecom import (
        HORIZONTAL_LINK_MAX,
        JUMP_LIST_MAX,
        build_sales_wecom_template_card,
    )

    monkeypatch.setattr(
        "app.services.sales_today_highlights.build_task_list_page_url",
        lambda **kwargs: "https://example.com/task",
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.build_visit_list_page_url",
        lambda **kwargs: "https://example.com/visit",
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.build_sales_today_highlights_url",
        lambda report_date, section: f"https://example.com/{section}",
    )
    assembled = {
        "report_date": "2026-11-11",
        "sections": [
            {
                "key": "immediate_today",
                "title": "立即处理",
                "url": "https://example.com/immediate",
                "items": [{"claim": "A", "why_today": "B"}],
            },
            {
                "key": "open_risks",
                "title": "关键发现",
                "url": "https://example.com/risks",
                "items": [{"claim": "r1"}, {"claim": "r2"}],
            },
            {
                "key": "next_week_confirm",
                "title": "下周待确认",
                "url": "https://example.com/next",
                "items": [{"claim": "n1"}],
            },
        ],
    }
    card = build_sales_wecom_template_card(assembled, owner_name="高娜")
    assert len(card["jump_list"]) <= JUMP_LIST_MAX
    assert card["jump_list"][0]["title"] == "查看"
    horizontal = card.get("horizontal_content_list") or []
    assert len(horizontal) <= HORIZONTAL_LINK_MAX
    assert all(row["keyname"] != row["value"] for row in horizontal)
    urls = {row["url"] for row in horizontal}
    assert "https://example.com/risks" in urls
    assert "https://example.com/next" in urls
    assert "https://example.com/task" in urls

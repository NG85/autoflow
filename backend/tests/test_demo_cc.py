"""DEMO_CC_* 与 OPS_CC_* 隔离：演示旁路不影响运维抄送白名单。"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.platform_notification_service import (
    PlatformNotificationService,
    _demo_cc_event_allowed,
    _parse_cc_provider,
    _parse_demo_cc_provider,
    _parse_ops_cc_provider,
)
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_WECOM


@pytest.fixture
def service():
    return PlatformNotificationService()


def test_parse_cc_provider_states():
    assert _parse_cc_provider("off") == (None, "disabled")
    assert _parse_cc_provider("feishu") == (PLATFORM_FEISHU, "ok")
    assert _parse_cc_provider("dingtalk") == (PLATFORM_DINGTALK, "ok")
    assert _parse_cc_provider("wecom") == (PLATFORM_WECOM, "ok")
    assert _parse_cc_provider("slack") == (None, "invalid")


def test_demo_cc_event_allowed_defaults_to_all(monkeypatch):
    monkeypatch.setattr("app.services.platform_notification_service.settings.DEMO_CC_EVENTS", None)
    assert _demo_cc_event_allowed("sales daily report") is True
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_EVENTS",
        ["all"],
    )
    assert _demo_cc_event_allowed("department daily report") is True


def test_demo_cc_event_allowed_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_EVENTS",
        ["Sales Daily Report", "company daily report"],
    )
    assert _demo_cc_event_allowed("sales daily report") is True
    assert _demo_cc_event_allowed("department daily report") is False


def test_ops_cc_still_rejects_non_allowlist(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        None,
    )
    with patch.object(service, "_cc_platform_card") as send:
        service._ops_cc_platform_card(
            {"type": "template"},
            scene="sales_daily",
            variant="kpi_card",
        )
        send.assert_not_called()


def test_ops_cc_forwards_company_report(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        None,
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_APP_ID",
        "cli_ops",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_APP_SECRET",
        "secret_ops",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_CHAT_IDS",
        ["oc_ops"],
    )
    with patch.object(service, "_cc_platform_card") as send:
        service._ops_cc_platform_card(
            {"type": "template"},
            scene="company_daily",
            variant="summary_md",
        )
        send.assert_called_once()
        assert send.call_args.kwargs["label"] == "Ops CC"
        assert send.call_args.kwargs["feishu_chat_ids"] == ["oc_ops"]
        assert send.call_args.kwargs["source"] == "company_daily.summary_md"


def test_demo_cc_independent_of_ops_provider(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "off",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_EVENTS",
        None,
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_APP_ID",
        "cli_demo",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_APP_SECRET",
        "secret_demo",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_CHAT_IDS",
        ["oc_demo"],
    )
    assert _parse_ops_cc_provider()[1] == "disabled"
    assert _parse_demo_cc_provider()[1] == "ok"

    with patch.object(service, "_cc_platform_card") as send:
        service._demo_cc_platform_card(
            {"type": "template"},
            source="department daily report",
            dingtalk_text="md",
        )
        send.assert_called_once()
        assert send.call_args.kwargs["label"] == "Demo CC"
        assert send.call_args.kwargs["feishu_chat_ids"] == ["oc_demo"]
        assert send.call_args.kwargs["dingtalk_text"] == "md"


def test_demo_cc_respects_event_filter(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_EVENTS",
        ["company daily report"],
    )
    with patch.object(service, "_cc_platform_card") as send:
        service._demo_cc_platform_card({"type": "template"}, source="sales daily report")
        send.assert_not_called()


def test_send_notifications_by_platform_invokes_demo_and_optional_ops(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_EVENTS",
        None,
    )

    with (
        patch.object(service, "_ops_cc_platform_card") as ops_cc,
        patch.object(service, "_demo_cc_platform_card") as demo_cc,
        patch.object(service, "_get_platform_tokens", return_value={PLATFORM_FEISHU: "tok"}),
        patch.object(service, "_send_messages_to_platform", return_value=(1, [])),
        patch.object(service, "_validate_platform_support", return_value=True),
    ):
        service._send_notifications_by_platform(
            recipients_by_platform={
                PLATFORM_FEISHU: [{"open_id": "ou_1", "name": "A", "type": "self"}]
            },
            card_content={"type": "template", "data": {}},
            notification_type="sales daily report",
        )

    # 未传 ops_cc_scene 时不走运维抄送；Demo 仍按 notification_type
    ops_cc.assert_not_called()
    demo_cc.assert_called_once()
    assert demo_cc.call_args.kwargs["source"] == "sales daily report"


def test_cc_platform_card_sends_feishu_chat(service, monkeypatch):
    feishu = MagicMock()
    feishu.get_tenant_access_token.return_value = "tok"
    with patch("app.services.platform_notification_service.feishu_client", feishu):
        service._cc_platform_card(
            {"type": "template", "data": {"template_id": "t1"}},
            source="sales daily report",
            label="Demo CC",
            target_platform=PLATFORM_FEISHU,
            feishu_app_id="cli_x",
            feishu_app_secret="sec",
            feishu_open_ids=None,
            feishu_chat_ids=["oc_demo"],
            dingtalk_app_id=None,
            dingtalk_app_secret=None,
            dingtalk_user_ids=None,
            dingtalk_chat_ids=None,
        )

    feishu.send_message.assert_called_once()
    args, kwargs = feishu.send_message.call_args
    assert args[0] == "oc_demo"
    assert kwargs["receive_id_type"] == "chat_id"


def test_ops_cc_allows_company_today_highlights(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        None,
    )
    with patch.object(service, "_cc_platform_card") as send:
        service._ops_cc_platform_card(
            {"type": "template"},
            scene="company_highlights",
            variant="today_highlights",
        )
        send.assert_called_once()
        assert send.call_args.kwargs["label"] == "Ops CC"
        assert send.call_args.kwargs["source"] == "company_highlights.today_highlights"


def test_cc_platform_card_sends_wecom_template_card(service):
    wecom = MagicMock()
    wecom.is_configured.return_value = True
    card = {"card_type": "text_notice", "main_title": {"title": "CXO"}}
    with patch("app.services.platform_notification_service.wecom_push_client", wecom):
        service._cc_platform_card(
            card,
            source="company_highlights.today_highlights",
            label="Ops CC",
            target_platform=PLATFORM_WECOM,
            feishu_app_id=None,
            feishu_app_secret=None,
            feishu_open_ids=None,
            feishu_chat_ids=None,
            dingtalk_app_id=None,
            dingtalk_app_secret=None,
            dingtalk_user_ids=None,
            dingtalk_chat_ids=None,
            wecom_user_ids=["user_ops"],
            wecom_chat_ids=["wr_demo"],
            wecom_base_url="https://ops-sia.example",
            wecom_token="tok",
        )

    wecom.is_configured.assert_called_with(base_url="https://ops-sia.example")
    assert wecom.send_message.call_count == 2
    first = wecom.send_message.call_args_list[0]
    assert first.args[0] == "user_ops"
    assert first.kwargs["msg_type"] == "template_card"
    assert first.kwargs["base_url"] == "https://ops-sia.example"
    second = wecom.send_message.call_args_list[1]
    assert second.args[0] == "wr_demo"
    assert second.kwargs["msg_type"] == "template_card"


def test_cc_platform_card_sends_wecom_markdown_from_dingtalk_text(service):
    wecom = MagicMock()
    wecom.is_configured.return_value = True
    with patch("app.services.platform_notification_service.wecom_push_client", wecom):
        service._cc_platform_card(
            {"type": "template"},
            source="company daily report",
            label="Ops CC",
            target_platform=PLATFORM_WECOM,
            feishu_app_id=None,
            feishu_app_secret=None,
            feishu_open_ids=None,
            feishu_chat_ids=None,
            dingtalk_app_id=None,
            dingtalk_app_secret=None,
            dingtalk_user_ids=None,
            dingtalk_chat_ids=None,
            dingtalk_text="## 公司日报",
            wecom_chat_ids=["wr_demo"],
            wecom_base_url="https://ops-sia.example",
            wecom_token="",
        )

    wecom.send_message.assert_called_once()
    assert wecom.send_message.call_args.kwargs["msg_type"] == "markdown"
    assert wecom.send_message.call_args.kwargs["base_url"] == "https://ops-sia.example"
    assert wecom.send_message.call_args.args[2] == "## 公司日报"


def test_cc_platform_card_wecom_skips_without_dedicated_base_url(service, monkeypatch):
    """运维旁路即使当前环境 WECOM_PUSH_* 已配，没有专用 BASE_URL 也不发。"""
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.WECOM_PUSH_BASE_URL",
        "https://tenant-sia.example",
    )
    wecom = MagicMock()
    wecom.is_configured.side_effect = (
        lambda *, base_url=None: bool((base_url or "").strip())
    )
    with patch("app.services.platform_notification_service.wecom_push_client", wecom):
        service._cc_platform_card(
            {"card_type": "text_notice"},
            source="company daily report",
            label="Ops CC",
            target_platform=PLATFORM_WECOM,
            feishu_app_id=None,
            feishu_app_secret=None,
            feishu_open_ids=None,
            feishu_chat_ids=None,
            dingtalk_app_id=None,
            dingtalk_app_secret=None,
            dingtalk_user_ids=None,
            dingtalk_chat_ids=None,
            wecom_chat_ids=["wr_demo"],
            wecom_base_url=None,
            wecom_token="tok",
        )

    wecom.is_configured.assert_called_with(base_url="")
    wecom.send_message.assert_not_called()


def test_build_cc_card_content_uses_wecom_kpi(service):
    card = service._build_cc_card_content_for_platform(
        target_platform=PLATFORM_WECOM,
        template_vars={"report_date": "2026-11-17"},
        notification_type="company daily report",
    )
    assert isinstance(card, dict)
    assert card.get("card_type") == "text_notice"


def test_company_today_highlights_ops_cc_once(monkeypatch):
    from app.services.company_today_highlights import handle_company_today_highlights

    calls = []
    demo_calls = []

    def _enable(policy_ids):
        monkeypatch.setattr(
            "app.services.company_today_highlights.load_report_push_policy",
            lambda: MagicMock(
                variant_enabled=lambda *_a, **_k: True,
                override_user_ids=lambda *_a, **_k: list(policy_ids),
            ),
        )

    _enable(["user-1"])
    monkeypatch.setattr(
        "app.services.company_today_highlights.check_today_highlights_quota",
        lambda *_a, **_k: (True, None),
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_cxo_insight_rows",
        lambda *_a, **_k: [MagicMock()],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.load_department_names",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.pick_report_sections",
        lambda *_a, **_k: [{"key": "cxo_efficiency_insight", "title": "公司人效", "body": "x"}],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.report_entries_from_sections",
        lambda *_a, **_k: [{"title": "公司人效", "url": "https://x", "body": "x"}],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.filter_opted_out_recipients",
        lambda _s, recipients, **_k: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.platform_notification_service.recipients_from_user_ids",
        lambda *_a, **_k: [
            {
                "user_id": "user-1",
                "open_id": "ou_1",
                "platform": "feishu",
                "name": "CXO",
                "receive_id_type": "open_id",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.deliver_company_highlight_card",
        lambda *_a, **_k: {"success": True},
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights.report_today_highlights_usage",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights._ops_cc_company_today_highlights",
        lambda assembled: calls.append(dict(assembled)),
    )
    monkeypatch.setattr(
        "app.services.company_today_highlights._demo_cc_company_today_highlights",
        lambda assembled: demo_calls.append(dict(assembled)),
    )

    result = handle_company_today_highlights(MagicMock(), report_date="2026-11-17")
    assert result["success_count"] == 1
    assert len(calls) == 1
    assert calls[0]["report_date"] == "2026-11-17"
    assert len(demo_calls) == 1


def test_mirror_demo_cc_noop_when_provider_off(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_PROVIDER",
        "off",
    )
    with patch.object(service, "_demo_cc_platform_card") as demo:
        service.mirror_demo_cc(
            source="sales today highlights",
            markdown_text="hello",
            title="t",
        )
        demo.assert_not_called()


def test_mirror_demo_cc_feishu_builds_from_markdown(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_EVENTS",
        None,
    )
    with patch.object(service, "_demo_cc_platform_card") as demo:
        service.mirror_demo_cc(
            source="comment notification",
            markdown_text="正文",
            title="标题",
        )
        demo.assert_called_once()
        assert demo.call_args.kwargs["source"] == "comment notification"
        assert isinstance(demo.call_args.args[0], dict)


def test_comment_notification_mirrors_demo(service, monkeypatch):
    profile = MagicMock()
    profile.oauth_user.provider = PLATFORM_FEISHU
    profile.oauth_user.open_id = "ou_1"
    monkeypatch.setattr(
        "app.services.platform_notification_service.user_profile_repo.get_by_user_id",
        lambda *_a, **_k: profile,
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.oauth_push_receive_id",
        lambda *_a, **_k: "ou_1",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.DEMO_CC_PROVIDER",
        "feishu",
    )
    with (
        patch.object(service, "_get_tenant_access_token", return_value="tok"),
        patch.object(service, "_send_message"),
        patch.object(service, "mirror_demo_cc") as mirror,
    ):
        result = service.send_weekly_followup_comment_notification(
            MagicMock(),
            recipient_user_id="00000000-0000-0000-0000-000000000001",
            message_text="标题\n评论：你好\nhttps://example.com",
        )
    assert result["success"] is True
    mirror.assert_called_once()
    assert mirror.call_args.kwargs["source"] == "comment notification"


def test_text_notification_mirrors_demo(service):
    with patch.object(service, "mirror_demo_cc") as mirror:
        with patch.object(service, "_group_recipients_by_platform", return_value={}):
            service.send_text_notification_to_recipients(
                recipients=[],
                message_text="hi",
                notification_type="company daily report empty text",
            )
        # no recipients → early return before mirror
        mirror.assert_not_called()

    with (
        patch.object(
            service,
            "_group_recipients_by_platform",
            return_value={PLATFORM_FEISHU: [{"open_id": "ou_1", "name": "A", "type": "x"}]},
        ),
        patch.object(service, "_get_platform_tokens", return_value={PLATFORM_FEISHU: "tok"}),
        patch.object(service, "_validate_platform_support", return_value=True),
        patch.object(service, "_send_message"),
        patch.object(service, "mirror_demo_cc") as mirror,
    ):
        service.send_text_notification_to_recipients(
            recipients=[{"open_id": "ou_1", "name": "A", "type": "x", "platform": PLATFORM_FEISHU}],
            message_text="无跟进",
            notification_type="company daily report empty text",
        )
    mirror.assert_called_once()
    assert mirror.call_args.kwargs["source"] == "company daily report empty text"
    assert mirror.call_args.kwargs["markdown_text"] == "无跟进"

"""OPS_CC_SCENES：按 scene / scene.variant 配置运维抄送白名单。"""

from unittest.mock import MagicMock, patch

import pytest

from app.platforms.constants import PLATFORM_FEISHU
from app.services.platform_notification_service import (
    OPS_CC_DEFAULT_SCENES,
    PlatformNotificationService,
    _parse_ops_cc_scene_entry,
    ops_cc_scene_allowed,
)


@pytest.fixture
def service():
    return PlatformNotificationService()


def test_parse_ops_cc_scene_entry():
    assert _parse_ops_cc_scene_entry("company_daily") == ("company_daily", None)
    assert _parse_ops_cc_scene_entry("company_daily.summary_md") == (
        "company_daily",
        "summary_md",
    )
    assert _parse_ops_cc_scene_entry("company_daily_report") == ("company_daily", None)
    assert _parse_ops_cc_scene_entry("") == (None, None)


def test_ops_cc_default_scenes_match_legacy_behavior(monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        None,
    )
    assert ops_cc_scene_allowed("company_daily", "summary_md") is True
    assert ops_cc_scene_allowed("company_daily", "kpi_card") is True
    assert ops_cc_scene_allowed("company_weekly", "visit_report") is True
    assert ops_cc_scene_allowed("company_highlights", "today_highlights") is True
    assert ops_cc_scene_allowed("department_daily", "summary_md") is False
    assert ops_cc_scene_allowed("sales_daily", "kpi_card") is False
    assert set(OPS_CC_DEFAULT_SCENES) == {
        "company_daily",
        "company_weekly",
        "company_highlights.today_highlights",
    }


def test_ops_cc_scenes_variant_precision(monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        ["company_daily.summary_md", "department_daily"],
    )
    assert ops_cc_scene_allowed("company_daily", "summary_md") is True
    assert ops_cc_scene_allowed("company_daily", "kpi_card") is False
    assert ops_cc_scene_allowed("department_daily", "summary_md") is True
    assert ops_cc_scene_allowed("department_daily", "kpi_card") is True


def test_ops_cc_platform_card_respects_scene_config(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        ["company_highlights.today_highlights"],
    )
    with patch.object(service, "_cc_platform_card") as send:
        service._ops_cc_platform_card(
            {"type": "template"},
            scene="company_daily",
            variant="summary_md",
        )
        send.assert_not_called()
        service._ops_cc_platform_card(
            {"type": "template"},
            scene="company_highlights",
            variant="today_highlights",
        )
        send.assert_called_once()
        assert send.call_args.kwargs["source"] == "company_highlights.today_highlights"


def test_send_notifications_by_platform_passes_scene(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_SCENES",
        ["company_daily.kpi_card"],
    )
    with (
        patch.object(service, "_ops_cc_platform_card") as ops_cc,
        patch.object(service, "_demo_cc_platform_card"),
        patch.object(service, "_get_platform_tokens", return_value={PLATFORM_FEISHU: "tok"}),
        patch.object(service, "_send_messages_to_platform", return_value=(1, [])),
        patch.object(service, "_validate_platform_support", return_value=True),
    ):
        service._send_notifications_by_platform(
            recipients_by_platform={
                PLATFORM_FEISHU: [{"open_id": "ou_1", "name": "A", "type": "self"}]
            },
            card_content={"type": "template", "data": {}},
            notification_type="company daily report",
            ops_cc_scene="company_daily",
            ops_cc_variant="kpi_card",
        )

    ops_cc.assert_called_once()
    assert ops_cc.call_args.kwargs["scene"] == "company_daily"
    assert ops_cc.call_args.kwargs["variant"] == "kpi_card"


def test_send_notifications_by_platform_skips_ops_without_scene(service, monkeypatch):
    monkeypatch.setattr(
        "app.services.platform_notification_service.settings.OPS_CC_PROVIDER",
        "feishu",
    )
    with (
        patch.object(service, "_ops_cc_platform_card") as ops_cc,
        patch.object(service, "_demo_cc_platform_card"),
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

    ops_cc.assert_not_called()

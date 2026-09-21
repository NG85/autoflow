"""日/周报推送变体开关。"""

from unittest.mock import MagicMock

from app.services.notification_scene_catalog import (
    SCENE_COMPANY_DAILY,
    SCENE_COMPANY_HIGHLIGHTS,
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_DAILY,
    SCENE_DEPARTMENT_HIGHLIGHTS,
    SCENE_SALES_DAILY,
    VARIANT_KPI_CARD,
    VARIANT_TODAY_HIGHLIGHTS,
    VARIANT_VISIT_REPORT,
)
from app.services.report_push_policy import (
    parse_report_push_policy,
    skip_kpi_card_result,
)


def test_empty_config_only_kpi_card():
    policy = parse_report_push_policy(None)
    assert policy.variant_enabled(SCENE_COMPANY_DAILY, VARIANT_KPI_CARD) is True
    assert policy.variant_enabled(SCENE_DEPARTMENT_DAILY, VARIANT_KPI_CARD) is True
    assert policy.variant_enabled(SCENE_COMPANY_WEEKLY, VARIANT_VISIT_REPORT) is False
    assert policy.variant_enabled(SCENE_COMPANY_DAILY, VARIANT_TODAY_HIGHLIGHTS) is False
    assert policy.variant_enabled(SCENE_COMPANY_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS) is False
    assert policy.variant_enabled(SCENE_DEPARTMENT_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS) is False
    assert policy.override_user_ids(SCENE_COMPANY_WEEKLY, VARIANT_VISIT_REPORT) == []


def test_bool_and_dict_variant_forms():
    policy = parse_report_push_policy(
        {
            "company_weekly": {
                "kpi_card": False,
                "visit_report": {
                    "enabled": True,
                    "recipient_user_ids": ["u1", "u1", "u2"],
                },
            },
            "department_weekly": {
                "kpi_card": True,
                "visit_report": True,
            },
        }
    )
    assert policy.variant_enabled(SCENE_COMPANY_WEEKLY, VARIANT_KPI_CARD) is False
    assert policy.variant_enabled(SCENE_COMPANY_WEEKLY, VARIANT_VISIT_REPORT) is True
    assert policy.override_user_ids(SCENE_COMPANY_WEEKLY, VARIANT_VISIT_REPORT) == ["u1", "u2"]
    assert policy.variant_enabled("department_weekly", VARIANT_VISIT_REPORT) is True


def test_today_highlights_only_on_sales_and_highlights_slots():
    policy = parse_report_push_policy(
        {
            "company_daily": {"today_highlights": True},
            "sales_daily": {"today_highlights": True},
            "company_highlights": {"today_highlights": True},
            "department_highlights": True,
            "company_weekly": {"today_highlights": True},
        }
    )
    assert policy.variant_enabled(SCENE_COMPANY_DAILY, VARIANT_TODAY_HIGHLIGHTS) is False
    assert policy.variant_enabled(SCENE_SALES_DAILY, VARIANT_TODAY_HIGHLIGHTS) is True
    assert policy.variant_enabled(SCENE_COMPANY_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS) is True
    assert policy.variant_enabled(SCENE_DEPARTMENT_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS) is True
    assert policy.variant_enabled(SCENE_COMPANY_WEEKLY, VARIANT_TODAY_HIGHLIGHTS) is False
    assert policy.variant_enabled(SCENE_COMPANY_HIGHLIGHTS, VARIANT_KPI_CARD) is False


def test_company_highlights_named_recipient_ids():
    policy = parse_report_push_policy(
        {
            "company_highlights": {
                "today_highlights": {
                    "enabled": True,
                    "recipient_user_ids": ["u1", "u1", "u2"],
                }
            }
        }
    )
    assert policy.variant_enabled(SCENE_COMPANY_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS) is True
    assert policy.override_user_ids(SCENE_COMPANY_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS) == [
        "u1",
        "u2",
    ]


def test_get_recipients_for_company_highlights_uses_policy_ids(monkeypatch):
    from app.services.platform_notification_service import PlatformNotificationService

    policy = parse_report_push_policy(
        {
            "company_highlights": {
                "today_highlights": {
                    "enabled": True,
                    "recipient_user_ids": ["u1"],
                }
            }
        }
    )
    monkeypatch.setattr(
        "app.services.report_push_policy.load_report_push_policy",
        lambda: policy,
    )
    svc = PlatformNotificationService()
    svc.recipients_from_user_ids = MagicMock(return_value=[{"user_id": "u1"}])
    result = svc.get_recipients_for_company_highlights(MagicMock())
    svc.recipients_from_user_ids.assert_called_once()
    assert svc.recipients_from_user_ids.call_args.args[1] == ["u1"]
    assert svc.recipients_from_user_ids.call_args.kwargs["recipient_type"] == "named_recipient"
    assert result == [{"user_id": "u1"}]


def test_get_recipients_for_company_highlights_empty_without_ids(monkeypatch):
    from app.services.platform_notification_service import PlatformNotificationService

    monkeypatch.setattr(
        "app.services.report_push_policy.load_report_push_policy",
        lambda: parse_report_push_policy({"company_highlights": True}),
    )
    svc = PlatformNotificationService()
    svc.recipients_from_user_ids = MagicMock()
    assert svc.get_recipients_for_company_highlights(MagicMock()) == []
    svc.recipients_from_user_ids.assert_not_called()


def test_visit_report_cannot_enable_on_daily_slots():
    policy = parse_report_push_policy(
        {
            "company_daily": {"kpi_card": True, "visit_report": True},
        }
    )
    assert policy.variant_enabled(SCENE_COMPANY_DAILY, VARIANT_VISIT_REPORT) is False


def test_alias_slot_names():
    policy = parse_report_push_policy(
        {"company_weekly_report": {"kpi_card": False}}
    )
    assert policy.variant_enabled(SCENE_COMPANY_WEEKLY, VARIANT_KPI_CARD) is False


def test_skip_kpi_card_result(monkeypatch):
    from app.services import report_push_policy as mod

    monkeypatch.setattr(
        mod,
        "load_report_push_policy",
        lambda: parse_report_push_policy({"company_daily": {"kpi_card": False}}),
    )
    skipped = skip_kpi_card_result(SCENE_COMPANY_DAILY)
    assert skipped["skipped"] is True
    assert skipped["skip_reason"] == "kpi_card_disabled"

    monkeypatch.setattr(
        mod,
        "load_report_push_policy",
        lambda: parse_report_push_policy({}),
    )
    assert skip_kpi_card_result(SCENE_COMPANY_DAILY) is None


def test_send_company_daily_skips_when_kpi_card_disabled(monkeypatch):
    from app.services.platform_notification_service import PlatformNotificationService

    monkeypatch.setattr(
        "app.services.report_push_policy.load_report_push_policy",
        lambda: parse_report_push_policy({"company_daily": {"kpi_card": False}}),
    )
    result = PlatformNotificationService().send_company_daily_report_notification(
        MagicMock(), {"report_date": "2026-09-21"}
    )
    assert result["skipped"] is True
    assert result["skip_reason"] == "kpi_card_disabled"

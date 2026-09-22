"""weekly_visit_report：读 crm_weekly_followup_summary.report_kind=visit_report 再发送。"""

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.api.routes.notification_schemas import WeeklyVisitReportPushRequest
from app.services.notification_scene_catalog import SCENE_COMPANY_WEEKLY
from app.services.report_push_policy import parse_report_push_policy
from app.services.report_ready_push import handle_report_ready


def test_weekly_visit_report_schema_defaults():
    payload = WeeklyVisitReportPushRequest(scene="company_weekly")
    assert payload.type == "weekly_visit_report"
    assert payload.variant == "visit_report"
    assert payload.week_start is None
    assert payload.delivery == "card"
    assert "content" not in WeeklyVisitReportPushRequest.model_fields


def test_weekly_visit_report_rejects_non_weekly_scene():
    with pytest.raises(ValueError, match="company_weekly"):
        handle_report_ready(
            MagicMock(),
            scene="company_daily",
            week_start=date(2026, 9, 13),
            week_end=date(2026, 9, 19),
        )


def test_weekly_visit_report_skips_when_variant_disabled():
    with patch(
        "app.services.report_ready_push.load_report_push_policy",
        return_value=parse_report_push_policy(
            {"company_weekly": {"kpi_card": True, "visit_report": False}}
        ),
    ):
        result = handle_report_ready(
            MagicMock(),
            scene=SCENE_COMPANY_WEEKLY,
            week_start="2026-09-13",
            week_end="2026-09-19",
        )

    assert result["skipped"] is True
    assert result["sent"] is False
    assert result["skip_reason"] == "variant_disabled"


def test_weekly_visit_report_skips_when_row_missing():
    db = MagicMock()
    db.exec.return_value.first.return_value = None
    db.exec.return_value.all.return_value = []
    with patch(
        "app.services.report_ready_push.load_report_push_policy",
        return_value=parse_report_push_policy(
            {"company_weekly": {"kpi_card": False, "visit_report": True}}
        ),
    ):
        result = handle_report_ready(
            db,
            scene=SCENE_COMPANY_WEEKLY,
            week_start="2026-09-13",
            week_end="2026-09-19",
        )

    assert result["skipped"] is True
    assert result["skip_reason"] == "summary_not_found"


def test_weekly_visit_report_sends_markdown_to_eligible_minus_opt_out():
    stored = MagicMock()
    stored.id = uuid4()
    stored.title = "周拜访报告"
    stored.summary_content = "**md**"
    stored.department_id = ""
    stored.department_name = ""
    db = MagicMock()
    db.exec.return_value.first.return_value = stored
    db.exec.return_value.all.return_value = [stored]

    uid_keep = str(uuid4())
    uid_out = str(uuid4())
    service = MagicMock()
    service.get_recipients_for_company_weekly_report.return_value = [
        {"user_id": uid_keep, "open_id": "ou_k"},
        {"user_id": uid_out, "open_id": "ou_o"},
    ]
    service.send_platform_notification.return_value = {"success": True}

    def _filter(_session, recipients, **_kwargs):
        return [r for r in recipients if r["user_id"] != uid_out]

    policy = parse_report_push_policy(
        {"company_weekly": {"kpi_card": False, "visit_report": True}}
    )
    with (
        patch(
            "app.services.report_ready_push.load_report_push_policy",
            return_value=policy,
        ),
        patch(
            "app.services.report_markdown_dispatch.load_report_push_policy",
            return_value=policy,
        ),
        patch(
            "app.services.report_markdown_dispatch.filter_opted_out_recipients",
            side_effect=_filter,
        ),
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
    ):
        result = handle_report_ready(
            db,
            scene="company_weekly",
            week_start=date(2026, 9, 13),
            week_end=date(2026, 9, 19),
        )

    assert result["sent"] is True
    assert result["success_count"] == 1
    assert result["summary_id"] == str(stored.id)
    service.send_platform_notification.assert_called_once()
    kwargs = service.send_platform_notification.call_args.kwargs
    assert kwargs["recipient_user_id"] == uid_keep
    assert kwargs["content"] == "**md**"
    assert kwargs["content_type"] == "markdown"
    assert kwargs["title"] == "APTSell 销售经营周报｜2026-09-13至2026-09-19"
    assert "agent_markdown" not in kwargs

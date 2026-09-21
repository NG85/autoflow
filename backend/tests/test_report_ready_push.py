"""report_ready：落库 visit_report，按策略发送，不恢复 agent_markdown。"""

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.api.routes.notification_schemas import ReportReadyPushRequest
from app.models.crm_weekly_followup_summary import REPORT_KIND_VISIT_REPORT
from app.services.notification_scene_catalog import SCENE_COMPANY_WEEKLY
from app.services.report_push_policy import parse_report_push_policy
from app.services.report_ready_push import handle_report_ready


def test_report_ready_schema_defaults():
    payload = ReportReadyPushRequest(
        scene="company_weekly",
        week_start="2026-09-13",
        week_end="2026-09-19",
        content="## 周拜访",
    )
    assert payload.type == "report_ready"
    assert payload.variant == "visit_report"
    assert payload.delivery == "card"


def test_report_ready_rejects_non_weekly_scene():
    with pytest.raises(ValueError, match="company_weekly"):
        handle_report_ready(
            MagicMock(),
            scene="company_daily",
            week_start=date(2026, 9, 13),
            week_end=date(2026, 9, 19),
            content="md",
        )


def test_report_ready_stores_even_when_variant_disabled():
    stored = MagicMock()
    stored.id = uuid4()
    stored.title = "周拜访报告"

    with (
        patch(
            "app.services.report_ready_push.crm_weekly_followup_service.upsert_summary",
            return_value=stored,
        ) as mock_upsert,
        patch(
            "app.services.report_ready_push.load_report_push_policy",
            return_value=parse_report_push_policy(
                {"company_weekly": {"kpi_card": True, "visit_report": False}}
            ),
        ),
    ):
        result = handle_report_ready(
            MagicMock(),
            scene=SCENE_COMPANY_WEEKLY,
            week_start="2026-09-13",
            week_end="2026-09-19",
            content="## hello",
            title="周拜访",
        )

    assert result["stored"] is True
    assert result["sent"] is False
    assert result["skip_reason"] == "variant_disabled"
    obj = mock_upsert.call_args.args[1]
    assert obj.report_kind == REPORT_KIND_VISIT_REPORT
    assert obj.summary_type == "company"
    assert obj.summary_content == "## hello"


def test_report_ready_sends_markdown_to_eligible_minus_opt_out():
    stored = MagicMock()
    stored.id = uuid4()
    stored.title = "周拜访报告"
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

    with (
        patch(
            "app.services.report_ready_push.crm_weekly_followup_service.upsert_summary",
            return_value=stored,
        ),
        patch(
            "app.services.report_ready_push.load_report_push_policy",
            return_value=parse_report_push_policy(
                {"company_weekly": {"kpi_card": False, "visit_report": True}}
            ),
        ),
        patch(
            "app.services.report_ready_push.filter_opted_out_recipients",
            side_effect=_filter,
        ),
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
    ):
        result = handle_report_ready(
            MagicMock(),
            scene="company_weekly",
            week_start=date(2026, 9, 13),
            week_end=date(2026, 9, 19),
            content="**md**",
        )

    assert result["sent"] is True
    assert result["success_count"] == 1
    service.send_platform_notification.assert_called_once()
    kwargs = service.send_platform_notification.call_args.kwargs
    assert kwargs["recipient_user_id"] == uid_keep
    assert kwargs["content_type"] == "markdown"
    assert "agent_markdown" not in kwargs

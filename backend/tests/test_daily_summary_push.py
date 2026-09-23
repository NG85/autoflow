"""日拜访报告 summary_md：从表读 Markdown，推给指定人或部门资格集。"""

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.api.routes.notification_schemas import DailyVisitReportPushRequest
from app.services.daily_summary_push import handle_daily_summary
from app.services.report_push_policy import parse_report_push_policy


def _enabled_policy(user_ids, *, department=False):
    slot = "department_daily" if department else "company_daily"
    return parse_report_push_policy(
        {
            slot: {
                "kpi_card": True,
                "summary_md": {
                    "enabled": True,
                    "recipient_user_ids": user_ids,
                },
            }
        }
    )


def _db_with_rows(rows):
    db = MagicMock()
    db.exec.return_value.all.return_value = rows
    db.exec.return_value.first.return_value = rows[0] if rows else None
    return db


def test_daily_visit_report_schema_defaults():
    payload = DailyVisitReportPushRequest()
    assert payload.type == "daily_visit_report"
    assert payload.scene == "company_daily"
    assert payload.variant == "summary_md"
    assert payload.report_date is None
    assert payload.department_id is None
    assert payload.delivery == "card"


def test_daily_visit_report_rejects_weekly_scene():
    with pytest.raises(ValueError, match="company_daily / department_daily"):
        handle_daily_summary(MagicMock(), scene="company_weekly")


def test_daily_summary_skips_when_variant_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.daily_summary_push.beijing_today_date",
        lambda: date(2026, 9, 21),
    )
    with patch(
        "app.services.daily_summary_push.load_report_push_policy",
        return_value=parse_report_push_policy({}),
    ):
        result = handle_daily_summary(MagicMock())

    assert result["skipped"] is True
    assert result["success"] is True
    assert result["skip_reason"] == "variant_disabled"
    assert result["report_date"] == "2026-09-20"


def test_daily_summary_skips_without_named_recipients():
    db = _db_with_rows([MagicMock(summary_content="## 正文")])
    with patch(
        "app.services.daily_summary_push.load_report_push_policy",
        return_value=parse_report_push_policy(
            {"company_daily": {"summary_md": True}}
        ),
    ):
        result = handle_daily_summary(
            db,
            report_date="2026-09-20",
        )

    assert result["skipped"] is True
    assert result["skip_reason"] == "no_named_recipients"


def test_daily_summary_skips_when_row_missing():
    db = _db_with_rows([])
    with patch(
        "app.services.daily_summary_push.load_report_push_policy",
        return_value=_enabled_policy(["u1"]),
    ):
        result = handle_daily_summary(db, report_date="2026-09-20")

    assert result["skipped"] is True
    assert result["skip_reason"] == "summary_not_found"


def test_daily_summary_skips_when_content_empty():
    row = MagicMock()
    row.summary_content = "  "
    row.department_id = ""
    row.department_name = ""
    db = _db_with_rows([row])
    with patch(
        "app.services.daily_summary_push.load_report_push_policy",
        return_value=_enabled_policy(["u1"]),
    ):
        result = handle_daily_summary(db, report_date="2026-09-20")

    assert result["skipped"] is True
    assert result["skip_reason"] == "empty_content"


def test_daily_summary_sends_markdown_to_named_minus_opt_out():
    uid_keep = str(uuid4())
    uid_out = str(uuid4())
    row = MagicMock()
    row.summary_content = "## 公司日报正文"
    row.department_id = ""
    row.department_name = ""
    db = _db_with_rows([row])

    service = MagicMock()
    service.recipients_from_user_ids.return_value = [
        {"user_id": uid_keep, "open_id": "ou_k"},
        {"user_id": uid_out, "open_id": "ou_o"},
    ]
    service.send_platform_notification.return_value = {"success": True}

    def _filter(_session, recipients, **_kwargs):
        return [r for r in recipients if r["user_id"] != uid_out]

    with (
        patch(
            "app.services.daily_summary_push.load_report_push_policy",
            return_value=_enabled_policy([uid_keep, uid_out]),
        ),
        patch(
            "app.services.report_markdown_dispatch.load_report_push_policy",
            return_value=_enabled_policy([uid_keep, uid_out]),
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
        result = handle_daily_summary(db, report_date="2026-09-20")

    assert result["sent"] is True
    assert result["skipped"] is False
    assert result["success_count"] == 1
    service.recipients_from_user_ids.assert_called_once()
    assert service.recipients_from_user_ids.call_args.kwargs["recipient_type"] == "named_recipient"
    service.send_platform_notification.assert_called_once()
    kwargs = service.send_platform_notification.call_args.kwargs
    assert kwargs["recipient_user_id"] == uid_keep
    assert kwargs["content"] == "## 公司日报正文"
    assert kwargs["content_type"] == "markdown"
    assert kwargs["title"] == "APTSell 销售经营日报｜2026-09-20"
    assert "agent_markdown" not in kwargs


def test_department_daily_summary_sends_to_leaders_when_no_named_override():
    uid = str(uuid4())
    row = MagicMock()
    row.summary_content = "## 部门日报"
    row.department_id = "dept-1"
    row.department_name = "销售部"
    db = _db_with_rows([row])

    service = MagicMock()
    service.resolve_department_report_recipients.return_value = [
        {"user_id": uid, "open_id": "ou_k"}
    ]
    service._filter_recipients_by_receive_permission.return_value = [
        {"user_id": uid, "open_id": "ou_k"}
    ]
    service._get_group_chats_by_department.return_value = []
    service.send_platform_notification.return_value = {"success": True}

    policy = parse_report_push_policy(
        {"department_daily": {"kpi_card": True, "summary_md": True}}
    )
    with (
        patch(
            "app.services.daily_summary_push.load_report_push_policy",
            return_value=policy,
        ),
        patch(
            "app.services.report_markdown_dispatch.load_report_push_policy",
            return_value=policy,
        ),
        patch(
            "app.services.report_markdown_dispatch.filter_opted_out_recipients",
            side_effect=lambda _s, recipients, **_k: recipients,
        ),
        patch(
            "app.services.report_markdown_dispatch.department_mirror_repo.get_department_name_by_id",
            return_value="销售部",
        ),
        patch(
            "app.services.platform_notification_service.platform_notification_service",
            service,
        ),
    ):
        result = handle_daily_summary(
            db,
            scene="department_daily",
            report_date="2026-09-20",
            department_id="dept-1",
        )

    assert result["sent"] is True
    assert result["success_count"] == 1
    service.resolve_department_report_recipients.assert_called_once()
    kwargs = service.send_platform_notification.call_args.kwargs
    assert kwargs["content"] == "## 部门日报"
    assert kwargs["title"] == "APTSell 销售经营日报｜2026-09-20"

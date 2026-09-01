from datetime import date, timezone

from app.core.config import CRMDailyReportVisitDateField, settings
from app.services.crm_statistics_service import CRMStatisticsService


def _predicate_sql(predicates) -> str:
    return " ".join(str(p) for p in predicates).lower()


def test_daily_report_predicates_use_followup_date_by_default(monkeypatch):
    monkeypatch.setattr(
        settings,
        "CRM_DAILY_REPORT_VISIT_DATE_FIELD",
        CRMDailyReportVisitDateField.VISIT_COMMUNICATION_DATE,
    )
    predicates = CRMStatisticsService()._daily_report_visit_date_predicates(date(2026, 8, 30))
    sql = _predicate_sql(predicates)
    assert "visit_communication_date" in sql
    assert "last_modified_time" not in sql


def test_daily_report_predicates_use_last_modified_time_utc_range(monkeypatch):
    monkeypatch.setattr(
        settings,
        "CRM_DAILY_REPORT_VISIT_DATE_FIELD",
        CRMDailyReportVisitDateField.LAST_MODIFIED_TIME,
    )
    predicates = CRMStatisticsService()._daily_report_visit_date_predicates(date(2026, 8, 30))
    assert predicates is not None
    sql = _predicate_sql(predicates)
    assert "last_modified_time" in sql
    assert "visit_communication_date" not in sql

    start = predicates[0].right.value
    end = predicates[1].right.value
    assert start.tzinfo is not None
    assert start.astimezone(timezone.utc).isoformat().startswith("2026-08-29T16:00:00")
    assert end.astimezone(timezone.utc).isoformat().startswith("2026-08-30T15:59:59")

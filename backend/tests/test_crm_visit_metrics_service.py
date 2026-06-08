from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.services.crm_visit_metrics_service import (
    CRMVisitMetricsService,
    VisitMetricsContext,
    _FACT_UPSERT_SQL,
    _row_to_int,
    crm_visit_metrics_service,
    default_rebuild_windows,
)


class SimpleNamespaceLike:
    def __init__(self, cnt: int):
        self.cnt = cnt


class _FakeExecResult:
    def __init__(self, rows=None, first=None):
        self._rows = rows or []
        self._first = first

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows

    def first(self):
        return self._first


class _FakeSession:
    def __init__(self, rows: list | None = None, headcount_first=None):
        self.rows = rows or []
        self.headcount_first = headcount_first if headcount_first is not None else SimpleNamespaceLike(42)
        self.exec_calls = 0
        self.executed_batches: list[list[dict]] = []
        self._connection = MagicMock()
        self._connection.execute.side_effect = self._record_execute

    def _record_execute(self, _sql, params):
        self.executed_batches.append(list(params))

    def exec(self, _sql, params=None):
        self.exec_calls += 1
        if "user_department_relation" in str(_sql):
            return _FakeExecResult(first=self.headcount_first)
        return _FakeExecResult(rows=self.rows)

    def connection(self):
        return self._connection


def _entry_detail_row(
    recorder_id: str,
    department_id: str,
    department_name: str,
    entry_date: date,
    weekday_iso: int,
    total_visits: int,
    call_high_visits: int,
):
    return (
        recorder_id,
        department_id,
        department_name,
        entry_date,
        weekday_iso,
        total_visits,
        call_high_visits,
    )


def _followup_row(
    followup_date: date,
    department_id: str,
    department_name: str,
    weekday_iso: int,
    total_visits: int,
    call_high_visits: int,
):
    return (
        followup_date,
        department_id,
        department_name,
        weekday_iso,
        total_visits,
        call_high_visits,
    )


def test_row_to_int_supports_scalar_and_row():
    assert _row_to_int(7) == 7
    assert _row_to_int(SimpleNamespaceLike(9)) == 9
    assert _row_to_int((11,)) == 11
    assert _row_to_int(None) == 0


def test_visit_metrics_context_caches_sales_headcount():
    session = _FakeSession(headcount_first=SimpleNamespaceLike(15))
    ctx = VisitMetricsContext()

    assert ctx.get_sales_headcount(session) == 15
    assert ctx.get_sales_headcount(session) == 15
    assert session.exec_calls == 1


def test_build_entry_week_fact_rows_rollup_from_single_detail_scan():
    service = CRMVisitMetricsService()
    week_start = date(2026, 6, 1)
    week_end = date(2026, 6, 7)
    detail_rows = [
        _entry_detail_row("rec-1", "dept-1", "Dept A", date(2026, 6, 2), 2, 3, 1),
        _entry_detail_row("rec-1", "dept-1", "Dept A", date(2026, 6, 3), 3, 2, 0),
        _entry_detail_row("rec-2", "dept-2", "Dept B", date(2026, 6, 2), 2, 1, 1),
    ]

    fact_rows, counts = service._build_entry_week_fact_rows(
        detail_rows,
        week_start=week_start,
        week_end=week_end,
        sales_headcount=100,
    )

    rec1_total = next(
        r for r in fact_rows if r["subject_id"] == "rec-1" and r["metric"] == "total_visits" and r["grain"] == "week"
    )
    assert rec1_total["value_int"] == 5

    company_weekday_tue = next(
        r
        for r in fact_rows
        if r["subject_type"] == "company" and r["weekday_iso"] == 2 and r["metric"] == "total_visits"
    )
    assert company_weekday_tue["value_int"] == 4

    sales_with_visits = next(r for r in fact_rows if r["metric"] == "sales_with_visits")
    sales_headcount = next(r for r in fact_rows if r["metric"] == "sales_headcount")
    assert sales_with_visits["value_int"] == 2
    assert sales_headcount["value_int"] == 100
    assert counts["weekly_rows"] == 4
    assert counts["daily_rows"] == 6


def test_rebuild_entry_week_uses_single_query_and_batch_upsert():
    service = CRMVisitMetricsService()
    session = _FakeSession(
        [
            _entry_detail_row("rec-1", "dept-1", "Dept A", date(2026, 6, 2), 2, 2, 1),
        ]
    )
    ctx = VisitMetricsContext(sales_headcount=50)

    with patch.object(service, "_batch_upsert_fact_metrics", side_effect=lambda _s, rows: len(rows)) as mock_batch:
        result = service.rebuild_entry_week(
            session,
            week_start=date(2026, 6, 1),
            week_end=date(2026, 6, 7),
            metrics_context=ctx,
        )

    assert session.exec_calls == 1
    assert mock_batch.call_count == 1
    assert result["weekly_rows"] == 2
    assert result["company_people_rows"] == 2


def test_execute_batch_upsert_chunks_rows():
    service = CRMVisitMetricsService()
    session = _FakeSession()
    rows = [{"id": str(i), "value_int": i} for i in range(5)]

    with patch("app.services.crm_visit_metrics_service._BATCH_UPSERT_SIZE", 2):
        written = service._batch_upsert_fact_metrics(session, rows)

    assert written == 5
    assert len(session.executed_batches) == 3


def test_rebuild_followup_builds_company_week_from_daily_rows():
    service = CRMVisitMetricsService()
    # 2026-06-02 是周二，所在周周日为 2026-06-01
    session = _FakeSession(
        [
            _followup_row(date(2026, 6, 2), "dept-1", "Dept A", 2, 4, 1),
            _followup_row(date(2026, 6, 3), "dept-2", "Dept B", 3, 2, 0),
        ]
    )

    with patch.object(service, "_batch_upsert_fact_metrics", side_effect=lambda _s, rows: len(rows)) as mock_batch:
        written = service.rebuild_followup_daily_department_metrics(
            session,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 7),
        )

    assert session.exec_calls == 1
    fact_rows = mock_batch.call_args.args[1]
    dept_rows = [r for r in fact_rows if r["subject_type"] == "department"]
    company_tue = next(
        r
        for r in fact_rows
        if r["subject_type"] == "company" and r["weekday_iso"] == 2 and r["metric"] == "total_visits"
    )
    company_wed = next(
        r
        for r in fact_rows
        if r["subject_type"] == "company" and r["weekday_iso"] == 3 and r["metric"] == "total_visits"
    )

    assert len(dept_rows) == 4
    assert company_tue["value_int"] == 4
    assert company_wed["value_int"] == 2
    assert written == len(fact_rows)


def test_default_rebuild_windows_returns_two_entry_weeks(monkeypatch):
    monkeypatch.setattr(
        "app.services.crm_visit_metrics_service.beijing_today_date",
        lambda: date(2026, 6, 4),
    )
    monkeypatch.setattr(
        "app.services.crm_visit_metrics_service.settings",
        type("S", (), {"CRM_VISIT_METRICS_FOLLOWUP_DAYS": 7})(),
    )

    windows = default_rebuild_windows()

    assert len(windows.entry_week_starts) == 2
    assert windows.entry_week_starts[1] == date(2026, 5, 31)
    assert windows.followup_end == date(2026, 6, 4)
    assert windows.followup_start == date(2026, 5, 29)


def test_service_singleton_is_crm_visit_metrics_service_instance():
    assert isinstance(crm_visit_metrics_service, CRMVisitMetricsService)


def test_batch_upsert_empty_returns_zero():
    service = CRMVisitMetricsService()
    session = _FakeSession()
    assert service._batch_upsert_fact_metrics(session, []) == 0
    assert session.executed_batches == []

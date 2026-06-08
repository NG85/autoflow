from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.crm_todo_metrics_service import (
    AssigneeMappingCache,
    CRMTodoMetricsService,
    _BATCH_UPSERT_SIZE,
    _FACT_UPSERT_SQL,
    _WEEKLY_UPSERT_SQL,
    _resolve_assignees,
    crm_todo_metrics_service,
    default_todo_metrics_windows,
)


class _FakeExecResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows: list | None = None):
        self.rows = rows or []
        self.executed_batches: list[list[dict]] = []
        self._connection = MagicMock()
        self._connection.execute.side_effect = self._record_execute

    def _record_execute(self, _sql, params):
        self.executed_batches.append(list(params))

    def exec(self, _sql, params=None):
        return _FakeExecResult(self.rows)

    def connection(self):
        return self._connection


def _stock_row(
    *,
    owner_id: str = "",
    owner_name: str = "",
    ai_status: str = "PENDING",
    no_due_cnt: int = 0,
    status_cnt: int = 0,
):
    # 与 DB Row 一致：支持按索引读取
    return (owner_id, owner_name, ai_status, no_due_cnt, status_cnt)


def _created_row(owner_id: str, owner_name: str, cnt: int):
    return SimpleNamespace(owner_id=owner_id, owner_name=owner_name, cnt=cnt)


@patch(
    "app.services.crm_todo_metrics_service.crm_sales_task_statistics_service._map_assignee_to_department_id",
    side_effect=[
        ({"owner-a": "user-a", "owner-b": "user-b"}, {"owner-a": "dept-a", "owner-b": "dept-b"}),
        ({"owner-c": "user-c"}, {"owner-c": "dept-c"}),
    ],
)
def test_assignee_mapping_cache_fetches_only_new_keys(mock_map):
    session = _FakeSession()
    cache = AssigneeMappingCache()

    cache.ensure(session, ["owner-a", "owner-b"])
    cache.ensure(session, ["owner-b", "owner-c"])

    assert mock_map.call_count == 2
    assert mock_map.call_args_list[0].args[1] == ["owner-a", "owner-b"]
    assert mock_map.call_args_list[1].args[1] == ["owner-c"]
    assert cache.raw_to_resolved["owner-a"] == "user-a"
    assert cache.raw_to_resolved["owner-c"] == "user-c"
    assert cache.raw_to_dept["owner-c"] == "dept-c"


@patch(
    "app.services.crm_todo_metrics_service.crm_sales_task_statistics_service._map_assignee_to_department_id",
    return_value=({"k1": "u1"}, {"k1": "d1"}),
)
def test_resolve_assignees_without_cache_calls_mapper(mock_map):
    session = _FakeSession()
    resolved, dept = _resolve_assignees(session, ["k1"], mapping_cache=None)

    mock_map.assert_called_once_with(session, ["k1"])
    assert resolved == {"k1": "u1"}
    assert dept == {"k1": "d1"}


def test_resolve_assignees_with_cache_reuses_mapper():
    session = _FakeSession()
    cache = AssigneeMappingCache()
    cache.raw_to_resolved = {"k1": "u1"}
    cache.raw_to_dept = {"k1": "d1"}
    cache._known = {"k1"}

    with patch(
        "app.services.crm_todo_metrics_service.crm_sales_task_statistics_service._map_assignee_to_department_id",
    ) as mock_map:
        resolved, dept = _resolve_assignees(session, ["k1"], mapping_cache=cache)

    mock_map.assert_not_called()
    assert resolved["k1"] == "u1"
    assert dept["k1"] == "d1"


def test_execute_batch_upsert_chunks_rows():
    service = CRMTodoMetricsService()
    session = _FakeSession()
    rows = [{"id": str(i), "value": i} for i in range(5)]

    with patch("app.services.crm_todo_metrics_service._BATCH_UPSERT_SIZE", 2):
        written = service._execute_batch_upsert(session, _WEEKLY_UPSERT_SQL, rows)

    assert written == 5
    assert len(session.executed_batches) == 3
    assert len(session.executed_batches[0]) == 2
    assert len(session.executed_batches[1]) == 2
    assert len(session.executed_batches[2]) == 1


def test_execute_batch_upsert_empty_returns_zero():
    service = CRMTodoMetricsService()
    session = _FakeSession()

    assert service._execute_batch_upsert(session, _FACT_UPSERT_SQL, []) == 0
    assert session.executed_batches == []


@patch(
    "app.services.crm_todo_metrics_service._resolve_assignees",
    return_value=(
        {"owner-a": "user-a", "owner-b": "user-b"},
        {"owner-a": "dept-a", "owner-b": "dept-b"},
    ),
)
def test_rebuild_stock_metrics_splits_no_due_and_status(mock_resolve):
    service = CRMTodoMetricsService()
    session = _FakeSession(
        [
            # 仅有 due_date 的任务：应写 status，不写 no_due_date
            _stock_row(owner_id="owner-a", ai_status="PENDING", no_due_cnt=0, status_cnt=2),
            # 有无 due_date 的任务：两类指标都写
            _stock_row(owner_id="owner-b", ai_status="COMPLETED", no_due_cnt=1, status_cnt=3),
        ]
    )
    week_start = date(2026, 6, 1)
    week_end = date(2026, 6, 7)

    with patch.object(service, "_batch_upsert_weekly_metrics", side_effect=lambda _s, rows: len(rows)) as mock_weekly, patch.object(
        service, "_batch_upsert_fact_metrics", side_effect=lambda _s, rows: len(rows)
    ) as mock_facts:
        result = service.rebuild_stock_metrics_for_week(session, week_start, week_end)

    mock_resolve.assert_called_once()
    weekly_rows = mock_weekly.call_args.args[1]
    fact_rows = mock_facts.call_args.args[1]

    owner_a_weekly_metrics = {r["metric"] for r in weekly_rows if r["assignee_id"] == "user-a"}
    owner_b_weekly_metrics = {r["metric"] for r in weekly_rows if r["assignee_id"] == "user-b" and r["department_id"] == "dept-b"}

    assert not any(m.startswith("no_due_date_stock_") for m in owner_a_weekly_metrics)
    assert "status_pending" in owner_a_weekly_metrics
    assert "no_due_date_stock_completed" in owner_b_weekly_metrics
    assert "no_due_date_stock_total" in owner_b_weekly_metrics
    assert "status_completed" in owner_b_weekly_metrics

    company_no_due_total = next(
        r["value"] for r in weekly_rows if r["assignee_id"] == "__ALL__" and r["metric"] == "no_due_date_stock_total"
    )
    assert company_no_due_total == 1
    assert result["rows"] == len(weekly_rows) + len(fact_rows)
    assert len(fact_rows) == len(weekly_rows)


@patch(
    "app.services.crm_todo_metrics_service._resolve_assignees",
    return_value=({"owner-a": "user-a"}, {"owner-a": "dept-a"}),
)
def test_rebuild_weekly_manual_created_uses_batch_upsert(mock_resolve):
    service = CRMTodoMetricsService()
    session = _FakeSession([_created_row("owner-a", "Alice", 4)])
    week_start = date(2026, 6, 1)
    week_end = date(2026, 6, 7)

    with patch.object(service, "_batch_upsert_weekly_metrics", side_effect=lambda _s, rows: len(rows)) as mock_weekly, patch.object(
        service, "_batch_upsert_fact_metrics", side_effect=lambda _s, rows: len(rows)
    ) as mock_facts:
        result = service.rebuild_weekly_manual_created(session, week_start, week_end)

    mock_resolve.assert_called_once()
    weekly_rows = mock_weekly.call_args.args[1]
    fact_rows = mock_facts.call_args.args[1]

    assert len(weekly_rows) == 2  # assignee + company
    assert weekly_rows[0]["metric"] == "tasks_created_manual"
    assert weekly_rows[0]["value"] == 4
    assert weekly_rows[1]["assignee_id"] == "__ALL__"
    assert weekly_rows[1]["value"] == 4
    assert len(fact_rows) == 2
    assert result["rows"] == 4


@patch(
    "app.services.crm_todo_metrics_service._resolve_assignees",
    return_value=({"owner-a": "user-a"}, {"owner-a": "dept-a"}),
)
def test_rebuild_weekly_completed_by_due_date_writes_facts_only(mock_resolve):
    service = CRMTodoMetricsService()
    session = _FakeSession(
        [
            SimpleNamespace(
                owner_id="owner-a",
                owner_name="Alice",
                data_source="MANUAL",
                cnt=2,
            )
        ]
    )

    with patch.object(service, "_batch_upsert_weekly_metrics") as mock_weekly, patch.object(
        service, "_batch_upsert_fact_metrics", side_effect=lambda _s, rows: len(rows)
    ) as mock_facts:
        result = service.rebuild_weekly_completed_by_due_date(
            session, date(2026, 6, 1), date(2026, 6, 7)
        )

    mock_weekly.assert_not_called()
    fact_rows = mock_facts.call_args.args[1]
    assignee_rows = [r for r in fact_rows if r["subject_type"] == "assignee" and r["subject_id"] == "user-a"]
    assert any(r["data_source"] == "MANUAL" and r["value_int"] == 2 for r in assignee_rows)
    assert any(r["data_source"] == "__ALL__" for r in assignee_rows)
    assert result["rows"] == len(fact_rows)


def test_default_todo_metrics_windows_returns_current_and_previous_week(monkeypatch):
    monkeypatch.setattr(
        "app.services.crm_todo_metrics_service.beijing_today_date",
        lambda: date(2026, 6, 4),  # 周三
    )

    windows = default_todo_metrics_windows()

    assert len(windows.week_starts) == 2
    assert windows.week_starts[1] == date(2026, 5, 31)  # 本周日
    assert windows.week_starts[0] == date(2026, 5, 24)  # 上周日


def test_weekly_and_fact_row_shapes():
    week_start = date(2026, 6, 1)
    week_end = date(2026, 6, 7)

    weekly = CRMTodoMetricsService._weekly_row(
        week_start,
        week_end,
        assignee_id="u1",
        department_id="d1",
        metric="status_pending",
        data_source="",
        value=3,
    )
    fact = CRMTodoMetricsService._fact_row(
        anchor="stock",
        grain="week",
        period_start=week_start,
        period_end=week_end,
        hour_of_day=0,
        subject_type="assignee",
        subject_id="u1",
        data_source="",
        metric="status_pending",
        value_int=3,
    )

    assert weekly["week_start"] == week_start
    assert weekly["value"] == 3
    assert len(weekly["id"]) == 32
    assert fact["anchor"] == "stock"
    assert fact["value_int"] == 3


def test_service_singleton_is_crm_todo_metrics_service_instance():
    assert isinstance(crm_todo_metrics_service, CRMTodoMetricsService)

"""拜访记录查询多字段排序。"""

from app.api.routes.crm.models import VisitRecordQueryRequest
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.repositories.visit_record import (
    _visit_record_order_by_clauses,
    _visit_record_sort_column,
)


def test_resolved_sorts_default_is_visit_date_then_created_time_desc():
    request = VisitRecordQueryRequest()
    assert request.resolved_sorts() == [
        ("visit_communication_date", "desc"),
        ("last_modified_time", "desc"),
    ]


def test_resolved_sorts_uses_sorts_in_order():
    request = VisitRecordQueryRequest(
        sorts=[
            {"field": "visit_communication_date", "direction": "desc"},
            {"field": "recorder", "direction": "asc"},
            {"field": "last_modified_time", "direction": "desc"},
        ],
    )
    assert request.resolved_sorts() == [
        ("visit_communication_date", "desc"),
        ("recorder", "asc"),
        ("last_modified_time", "desc"),
    ]


def test_resolved_sorts_dedupes_and_skips_empty_fields():
    request = VisitRecordQueryRequest(
        sorts=[
            {"field": "  ", "direction": "asc"},
            {"field": "recorder", "direction": "asc"},
            {"field": "recorder", "direction": "desc"},
        ],
    )
    assert request.resolved_sorts() == [("recorder", "asc")]


def test_resolved_sorts_empty_sorts_uses_default():
    request = VisitRecordQueryRequest(sorts=[])
    assert request.resolved_sorts() == [
        ("visit_communication_date", "desc"),
        ("last_modified_time", "desc"),
    ]


def test_visit_record_sort_column_aliases():
    assert _visit_record_sort_column("department", "level-col") is CRMSalesVisitRecord.recorder_department_name
    assert _visit_record_sort_column("customer_level", "level-col") == "level-col"
    assert _visit_record_sort_column("not_a_column", "level-col") is None


def test_visit_record_order_by_clauses_uses_requested_fields_and_directions():
    clauses = _visit_record_order_by_clauses(
        [("recorder", "asc"), ("visit_communication_date", "desc"), ("unknown", "asc")],
        customer_level_col="level-col",
    )
    assert len(clauses) == 2
    assert "recorder" in str(clauses[0]).lower()
    assert "asc" in str(clauses[0]).lower()
    assert "visit_communication_date" in str(clauses[1]).lower()
    assert "desc" in str(clauses[1]).lower()


def test_visit_record_order_by_clauses_fallback_when_all_invalid():
    clauses = _visit_record_order_by_clauses(
        [("not_a_column", "asc")],
        customer_level_col="level-col",
    )
    assert len(clauses) == 2
    assert "visit_communication_date" in str(clauses[0]).lower()
    assert "last_modified_time" in str(clauses[1]).lower()

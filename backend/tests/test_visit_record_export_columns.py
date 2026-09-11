"""跟进记录导出列选择。"""

from types import SimpleNamespace

import pytest

from app.api.routes.crm.models import VisitRecordQueryRequest
from app.utils.visit_record_export import (
    UnknownVisitRecordExportColumn,
    VISIT_RECORD_EXPORT_COLUMN_KEYS,
    build_export_row,
    get_export_headers,
    pick_export_row_values,
    resolve_export_columns,
)


def test_resolve_export_columns_default_is_all():
    assert resolve_export_columns(None) == list(VISIT_RECORD_EXPORT_COLUMN_KEYS)
    assert resolve_export_columns([]) == list(VISIT_RECORD_EXPORT_COLUMN_KEYS)
    assert resolve_export_columns(["", "  "]) == list(VISIT_RECORD_EXPORT_COLUMN_KEYS)


def test_resolve_export_columns_keeps_requested_order_and_dedupes():
    assert resolve_export_columns(
        ["recorder", " followup_object_name ", "recorder", "visit_communication_date"]
    ) == ["recorder", "followup_object_name", "visit_communication_date"]


def test_resolve_export_columns_rejects_unknown():
    with pytest.raises(UnknownVisitRecordExportColumn, match="not_a_column"):
        resolve_export_columns(["recorder", "not_a_column"])


def test_get_export_headers_zh_and_en():
    keys = ["record_id", "followup_object_name", "is_first_visit"]
    assert get_export_headers(keys, "zh") == ["ID", "跟进对象", "是否首次拜访"]
    assert get_export_headers(keys, "en") == ["ID", "Follow-up Object", "First Visit"]


def test_pick_export_row_values_filters_and_orders():
    row = {
        "record_id": "abc",
        "recorder": "张三",
        "followup_record": "内容",
        "remarks": "备注",
    }
    assert pick_export_row_values(row, ["recorder", "record_id"]) == ["张三", "abc"]


def _export_item(**overrides):
    defaults = dict(
        id=1,
        record_id="rec-1",
        customer_level="KA",
        followup_object_name="客户A",
        followup_object_id="acc-1",
        customer_attribute="终端客户",
        is_first_visit=True,
        is_call_high=False,
        account_name="客户A",
        account_id="acc-1",
        partner_name=None,
        partner_id=None,
        external_collaboration_partner_name=None,
        external_collaboration_partner_id=None,
        opportunity_name="商机A",
        opportunity_id="opp-1",
        visit_communication_date="2026-01-01",
        recorder="张三",
        department="销售一部",
        contacts=None,
        contact_position="经理",
        contact_name="李四",
        collaborative_participants="",
        visit_communication_method="拜访",
        visit_purpose="需求沟通",
        attachment=None,
        followup_record="记录",
        followup_record_zh="中文记录",
        followup_record_en="EN record",
        followup_quality_level_zh="优秀",
        followup_quality_level_en="Excellent",
        followup_quality_reason_zh="详情",
        followup_quality_reason_en="Details",
        next_steps="下一步",
        next_steps_zh="中文下一步",
        next_steps_en="EN next",
        next_steps_quality_level_zh="良好",
        next_steps_quality_level_en="Good",
        next_steps_quality_reason_zh="依据",
        next_steps_quality_reason_en="Reason",
        assessment_flag="green",
        record_type="Customer Visit",
        visit_type="form",
        remarks="备注",
        comments=[{"type": "comment", "author": "A", "content": "c1", "created_at": "2026-01-01"}],
        last_modified_time="2026-01-02 10:00:00",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_export_row_contains_all_columns_and_subset_works():
    item = _export_item()
    row = build_export_row(item, filing_opportunity_number="F-001", language="zh")
    assert set(row) == set(VISIT_RECORD_EXPORT_COLUMN_KEYS)
    assert row["record_id"] == "rec-1"
    assert row["opportunity_number"] == "F-001"
    assert row["is_first_visit"] == "是"
    assert row["is_call_high"] == "否"
    assert row["assessment_flag"] == "🟢"
    assert row["record_type"] == "客户拜访"
    assert pick_export_row_values(row, ["opportunity_name", "recorder"]) == ["商机A", "张三"]


def test_build_export_row_english_localization():
    item = _export_item()
    row = build_export_row(item, language="en")
    assert row["is_first_visit"] == "Yes"
    assert row["followup_record"] == "EN record"
    assert row["record_type"] == "Customer Visit"


def test_visit_record_query_request_accepts_export_columns():
    request = VisitRecordQueryRequest(
        export_columns=["recorder", "followup_object_name"],
        language="zh",
    )
    assert request.export_columns == ["recorder", "followup_object_name"]
    assert VisitRecordQueryRequest().export_columns is None

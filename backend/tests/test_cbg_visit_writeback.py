"""CBG 拜访记录回写：输出字段与线索 lead_ids 映射。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.wb_visit_requests import CbgVisitRecordBatchCreateRequest
from app.services.crm_writeback_service import CrmWritebackService


def _record(**overrides):
    recorder_id = uuid4()
    base = {
        "id": 1,
        "record_id": "cbg_visit_001",
        "followup_object_type": "end_customer",
        "followup_object_id": "acc_001",
        "followup_object_name": "测试客户",
        "account_id": "acc_001",
        "account_name": "测试客户",
        "partner_id": None,
        "partner_name": None,
        "external_collaboration_partner_id": None,
        "external_collaboration_partner_name": None,
        "opportunity_id": "opp_001",
        "opportunity_name": "测试商机",
        "recorder_id": recorder_id,
        "recorder": "张三",
        "contacts": None,
        "contact_name": None,
        "contact_position": None,
        "collaborative_participants": None,
        "visit_communication_date": date(2026, 8, 17),
        "visit_communication_method": "电话/录音",
        "followup_record": "跟进说明",
        "followup_record_zh": None,
        "followup_content": None,
        "next_steps": None,
        "next_steps_zh": None,
        "visit_purpose": None,
        "expectation_achieved": None,
        "remarks": None,
        "is_first_visit": None,
        "is_call_high": None,
        "subject": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _session_without_crm_user() -> MagicMock:
    session = MagicMock()
    session.exec.return_value.first.return_value = None
    return session


def test_generate_cbg_lead_visit_writes_lead_ids_not_account_ids():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record(
        followup_object_type="lead",
        followup_object_id="5ff3e47230bbb7000193f7e7",
        followup_object_name="测试线索",
        account_id=None,
        account_name=None,
        opportunity_id=None,
    )

    batch = svc.generate_cbg_visit_requests(session, [record])
    assert len(batch.records) == 1
    req = batch.records[0]
    assert req.lead_ids == ["5ff3e47230bbb7000193f7e7"]
    assert req.account_ids is None
    assert req.opportunity_ids is None
    assert req.source_record_id == "cbg_visit_001"
    assert req.record_type == "电话/微信跟进"

    payload = CbgVisitRecordBatchCreateRequest(records=batch.records).model_dump(
        exclude_none=True
    )
    assert payload["records"][0]["lead_ids"] == ["5ff3e47230bbb7000193f7e7"]
    assert "account_ids" not in payload["records"][0]


def test_generate_cbg_account_visit_keeps_account_and_opportunity():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record()

    batch = svc.generate_cbg_visit_requests(session, [record])
    assert len(batch.records) == 1
    req = batch.records[0]
    assert req.lead_ids is None
    assert req.account_ids == ["acc_001"]
    assert req.opportunity_ids == ["opp_001"]


def test_generate_cbg_skips_when_no_account_opportunity_or_lead():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record(
        followup_object_type=None,
        followup_object_id=None,
        account_id=None,
        partner_id=None,
        opportunity_id=None,
    )

    batch = svc.generate_cbg_visit_requests(session, [record])
    assert batch.records == []

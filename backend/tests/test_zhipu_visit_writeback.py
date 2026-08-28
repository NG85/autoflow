"""智谱拜访记录回写：标准字段名与跟进对象映射。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.wb_visit_requests import (
    ZhipuVisitRecordBatchCreateRequest,
    ZhipuVisitRecordCreateRequest,
)
from app.services.crm_writeback_service import (
    CrmVisitWritebackClient,
    CrmWritebackService,
)


def _record(**overrides):
    recorder_id = uuid4()
    base = {
        "id": 1,
        "record_id": "zhipu_visit_001",
        "followup_object_type": "end_customer",
        "followup_object_id": "6034xxxxxxxx",
        "followup_object_name": "测试客户",
        "account_id": "6034xxxxxxxx",
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
        "visit_communication_method": "外出拜访客户",
        "followup_record": "拜访内容",
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


def test_generate_zhipu_account_visit_maps_standard_fields():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record()

    batch = svc.generate_zhipu_visit_requests(session, [record])
    assert len(batch.visit_records) == 1
    req = batch.visit_records[0]
    assert req.source_record_id == "zhipu_visit_001"
    assert req.followup_object_id == "6034xxxxxxxx"
    assert req.followup_object_type is None
    assert req.opportunity_id == "opp_001"
    assert req.visit_communication_method == "外出拜访客户"
    assert "拜访内容" in req.followup_record
    assert req.recorder_id is None

    payload = ZhipuVisitRecordBatchCreateRequest(
        visit_records=batch.visit_records
    ).model_dump(exclude_none=True)
    item = payload["visit_records"][0]
    assert "record_id" not in item
    assert "followup_object_type" not in item
    assert "recorder_id" not in item
    assert item["followup_object_id"] == "6034xxxxxxxx"


def test_generate_zhipu_lead_visit_writes_lead_object():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record(
        followup_object_type="lead",
        followup_object_id="5ff3e47230bbb7000193f7e7",
        followup_object_name="测试线索",
        account_id=None,
        account_name=None,
        opportunity_id=None,
        visit_communication_method="微信沟通",
    )

    batch = svc.generate_zhipu_visit_requests(session, [record])
    assert len(batch.visit_records) == 1
    req = batch.visit_records[0]
    assert req.followup_object_id == "5ff3e47230bbb7000193f7e7"
    assert req.followup_object_type == "lead"
    assert req.opportunity_id is None
    assert req.visit_communication_method == "微信沟通"

    payload = ZhipuVisitRecordBatchCreateRequest(
        visit_records=batch.visit_records
    ).model_dump(exclude_none=True)
    assert "opportunity_id" not in payload["visit_records"][0]


def test_generate_zhipu_opportunity_only_visit():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record(
        followup_object_type=None,
        followup_object_id=None,
        account_id=None,
        partner_id=None,
        opportunity_id="opp_only",
        visit_communication_method="在公司接待客户",
    )

    batch = svc.generate_zhipu_visit_requests(session, [record])
    assert len(batch.visit_records) == 1
    req = batch.visit_records[0]
    assert req.followup_object_id is None
    assert req.followup_object_type is None
    assert req.opportunity_id == "opp_only"


def test_generate_zhipu_skips_when_no_object_or_opportunity():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record(
        followup_object_type=None,
        followup_object_id=None,
        account_id=None,
        partner_id=None,
        opportunity_id=None,
    )

    batch = svc.generate_zhipu_visit_requests(session, [record])
    assert batch.visit_records == []


def test_generate_zhipu_skips_when_visit_method_empty():
    svc = CrmWritebackService()
    session = _session_without_crm_user()
    record = _record(visit_communication_method="  ")

    batch = svc.generate_zhipu_visit_requests(session, [record])
    assert batch.visit_records == []


def test_generate_zhipu_resolves_recorder_crm_user_id():
    svc = CrmWritebackService()
    session = MagicMock()
    session.exec.return_value.first.return_value = ("1527",)
    record = _record()

    batch = svc.generate_zhipu_visit_requests(session, [record])
    assert batch.visit_records[0].recorder_id == "1527"


def test_batch_zhipu_visit_create_posts_to_fxiaoke_path(monkeypatch):
    captured = {}

    class FakeResponse:
        text = '{"created": 1}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"created": 1}

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.crm_writeback_service.httpx.Client", FakeClient
    )

    client = CrmVisitWritebackClient("https://host")
    req = ZhipuVisitRecordBatchCreateRequest(
        visit_records=[
            ZhipuVisitRecordCreateRequest(
                followup_record="拜访内容",
                visit_communication_method="外出拜访客户",
                followup_object_id="6034xxxxxxxx",
                source_record_id="recv_abc123",
                recorder_id="1527",
            )
        ]
    )
    result = client.batch_zhipu_visit_create(req)
    assert captured["url"] == "https://host/crm-fxiaoke/zhipu/sale-record/batch"
    item = captured["json"]["visit_records"][0]
    assert item["source_record_id"] == "recv_abc123"
    assert item["followup_record"] == "拜访内容"
    assert item["visit_communication_method"] == "外出拜访客户"
    assert item["recorder_id"] == "1527"
    assert result["success"] is True

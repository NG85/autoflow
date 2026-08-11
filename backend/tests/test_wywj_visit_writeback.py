"""网眼云捷（WYWJ）拜访记录回写请求生成测试。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.wb_visit_requests import WywjVisitRecordCreateRequest
from app.services.crm_writeback_service import CrmWritebackService


def _record(**overrides):
    base = {
        "id": 1,
        "record_id": "form_20260729_001",
        "followup_object_type": "end_customer",
        "followup_object_id": "rec27oBKqTBiHk",
        "followup_object_name": "测试客户",
        "account_id": None,
        "account_name": None,
        "partner_id": None,
        "partner_name": None,
        "opportunity_id": "rec27oBKVar322",
        "recorder_id": uuid4(),
        "collaborative_participants": None,
        "visit_communication_date": date(2026, 7, 28),
        "contacts": [{"name": "业务负责-江", "position": "经理", "contact_id": "c1"}],
        "contact_name": "旧字段应忽略",
        "followup_record_zh": "本期沟通总结",
        "followup_record": None,
        "followup_content": None,
        "next_steps_zh": "下周发送方案",
        "next_steps": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_format_wywj_contact_name_from_contacts_only():
    record = _record(
        contacts=[
            {"name": "张三", "position": "总监"},
            {"name": "李四"},
            {"name": "  "},
            "bad",
        ],
        contact_name="旧字段",
    )
    assert CrmWritebackService._format_wywj_contact_name(record) == "张三、李四"


def test_format_wywj_contact_name_empty_when_no_contacts():
    record = _record(contacts=None, contact_name="旧字段")
    assert CrmWritebackService._format_wywj_contact_name(record) is None


def test_generate_wywj_only_form_or_link_record_id():
    svc = CrmWritebackService()
    session = MagicMock()
    svc._resolve_feishu_open_ids = MagicMock(return_value={})

    form_record = _record(id=1, record_id="form_20260729_001")
    link_record = _record(id=2, record_id="link_20260801_abc")
    other_record = _record(id=3, record_id="recImported123")
    empty_record = _record(id=4, record_id=None)

    batch = svc.generate_wywj_visit_requests(
        session, [form_record, link_record, other_record, empty_record]
    )
    assert [r.source_record_id for r in batch.visit_records] == [
        "form_20260729_001",
        "link_20260801_abc",
    ]
    # eligible 记录的 recorder/参与人 open_id 一次性批量解析
    assert svc._resolve_feishu_open_ids.call_count == 1


def test_generate_wywj_visit_requests_maps_fields():
    svc = CrmWritebackService()
    session = MagicMock()
    recorder_id = uuid4()
    collab_id = uuid4()
    record = _record(
        recorder_id=recorder_id,
        collaborative_participants=[
            {"name": "协同A", "ask_id": str(collab_id)},
            {"name": "外部人", "ask_id": None},
        ],
    )

    svc._resolve_feishu_open_ids = MagicMock(
        side_effect=lambda _session, user_ids: {
            uid: f"ou_{uid.replace('-', '')[:8]}" for uid in user_ids
        }
    )

    batch = svc.generate_wywj_visit_requests(session, [record])
    assert len(batch.visit_records) == 1
    req = batch.visit_records[0]
    assert isinstance(req, WywjVisitRecordCreateRequest)
    assert req.source_record_id == "form_20260729_001"
    assert req.followup_object_id == "rec27oBKqTBiHk"
    assert req.opportunity_id == "rec27oBKVar322"
    assert req.visit_communication_date == "2026-07-28"
    assert req.contact_name == "业务负责-江"
    assert req.followup_record == "本期沟通总结"
    assert req.next_steps == "下周发送方案"
    assert req.recorder_id == f"ou_{str(recorder_id).replace('-', '')[:8]}"
    assert req.collaborative_participants == [
        f"ou_{str(collab_id).replace('-', '')[:8]}"
    ]
    looked_up = set(svc._resolve_feishu_open_ids.call_args[0][1])
    assert str(recorder_id) in looked_up
    assert str(collab_id) in looked_up
    assert svc._resolve_feishu_open_ids.call_count == 1


def test_generate_wywj_skips_when_followup_object_missing():
    svc = CrmWritebackService()
    session = MagicMock()
    svc._resolve_feishu_open_ids = MagicMock(return_value={})
    record = _record(
        followup_object_type=None,
        followup_object_id=None,
        account_id=None,
        partner_id=None,
    )
    batch = svc.generate_wywj_visit_requests(session, [record])
    assert batch.visit_records == []
    assert svc._resolve_feishu_open_ids.call_count == 0


def test_wywj_batch_payload_exclude_none():
    req = WywjVisitRecordCreateRequest(
        source_record_id="form_1",
        followup_object_id="recAccount",
        recorder_id="ou_abc",
        visit_communication_date="2026-07-28",
        followup_record="内容",
    )
    payload = {"visit_records": [req.model_dump(exclude_none=True)], "partial_fail": True}
    item = payload["visit_records"][0]
    assert "opportunity_id" not in item
    assert "collaborative_participants" not in item
    assert "contact_name" not in item
    assert item["source_record_id"] == "form_1"

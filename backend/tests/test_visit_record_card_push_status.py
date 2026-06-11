"""visit_record_card_push_status 与 link 链路辅助逻辑测试。"""

from app.services.visit_record_card_push_status import (
    VisitRecordCardPushStatus,
    link_content_status_from_card_push,
)


def test_link_content_status_processing_while_card_pipeline_runs():
    assert (
        link_content_status_from_card_push(VisitRecordCardPushStatus.CONTENT_PROCESSING)
        == "processing"
    )
    assert link_content_status_from_card_push(VisitRecordCardPushStatus.PENDING) == "processing"
    assert (
        link_content_status_from_card_push(VisitRecordCardPushStatus.AWAITING_CALLBACK)
        == "processing"
    )


def test_link_content_status_terminal_states():
    assert link_content_status_from_card_push(VisitRecordCardPushStatus.PUSHED) == "completed"
    assert link_content_status_from_card_push(VisitRecordCardPushStatus.FAILED) == "failed"

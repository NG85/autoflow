"""visit_record_card_push_status 与 link 链路辅助逻辑测试。"""

from app.services.visit_record_card_push_status import (
    VisitRecordCardPushStatus,
    failed_recipients_to_recipients_by_platform,
    is_terminal_card_push_status,
    link_content_status_from_card_push,
    resolve_card_push_status_after_retry,
    resolve_card_push_status_from_notification_result,
    should_skip_duplicate_card_push_callback,
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
    assert (
        link_content_status_from_card_push(VisitRecordCardPushStatus.PARTIAL_PUSHED)
        == "completed"
    )
    assert link_content_status_from_card_push(VisitRecordCardPushStatus.FAILED) == "failed"


def test_resolve_card_push_status_from_notification_result():
    assert (
        resolve_card_push_status_from_notification_result(
            success_count=0,
            recipients_count=2,
            failed_recipients=[{"name": "A"}],
        )
        == VisitRecordCardPushStatus.FAILED
    )
    assert (
        resolve_card_push_status_from_notification_result(
            success_count=2,
            recipients_count=2,
            failed_recipients=[],
        )
        == VisitRecordCardPushStatus.PUSHED
    )
    assert (
        resolve_card_push_status_from_notification_result(
            success_count=1,
            recipients_count=2,
            failed_recipients=[{"name": "B"}],
        )
        == VisitRecordCardPushStatus.PARTIAL_PUSHED
    )


def test_is_terminal_card_push_status():
    assert is_terminal_card_push_status(VisitRecordCardPushStatus.PUSHED) is True
    assert is_terminal_card_push_status(VisitRecordCardPushStatus.PARTIAL_PUSHED) is True
    assert is_terminal_card_push_status(VisitRecordCardPushStatus.FAILED) is False
    assert is_terminal_card_push_status(VisitRecordCardPushStatus.AWAITING_CALLBACK) is False


def test_should_skip_duplicate_card_push_callback():
    assert should_skip_duplicate_card_push_callback(VisitRecordCardPushStatus.PUSHED) is True
    assert should_skip_duplicate_card_push_callback(VisitRecordCardPushStatus.PARTIAL_PUSHED) is False


def test_resolve_card_push_status_after_retry():
    assert (
        resolve_card_push_status_after_retry(
            total_recipients=2,
            previously_failed_count=1,
            retry_success_count=1,
            retry_failed_recipients=[],
        )
        == VisitRecordCardPushStatus.PUSHED
    )
    assert (
        resolve_card_push_status_after_retry(
            total_recipients=2,
            previously_failed_count=1,
            retry_success_count=0,
            retry_failed_recipients=[{"name": "B"}],
        )
        == VisitRecordCardPushStatus.PARTIAL_PUSHED
    )


def test_failed_recipients_to_recipients_by_platform():
    grouped = failed_recipients_to_recipients_by_platform(
        [
            {
                "open_id": "ou_1",
                "name": "销售",
                "type": "recorder",
                "platform": "feishu",
                "receive_id_type": "open_id",
                "error": "boom",
            }
        ]
    )
    assert grouped == {
        "feishu": [
            {
                "open_id": "ou_1",
                "name": "销售",
                "type": "recorder",
                "receive_id_type": "open_id",
                "platform": "feishu",
            }
        ]
    }

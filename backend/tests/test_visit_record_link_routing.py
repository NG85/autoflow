"""拜访 link 类型路由：仅钉钉听记异步，其他类型保持同步路径。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.platforms.utils.url_parser import parse_dingtalk_transcribe_url
from app.services.document_processing_service import DocumentProcessingService
from app.tasks.dingtalk_transcribe import process_dingtalk_transcribe_visit_record


TRANSCRIBE_URL = "https://shanji.dingtalk.com/app/transcribes/task001"
FEISHU_URL = "https://example.feishu.cn/docx/abc123"
ROOM_CODE = "123456789"


def _is_transcribe_async_visit_path(visit_url: str) -> bool:
    """与 router 中听记分支判断一致。"""
    return bool(parse_dingtalk_transcribe_url(visit_url))


def test_only_shanji_url_uses_async_visit_path():
    assert _is_transcribe_async_visit_path(TRANSCRIBE_URL) is True
    assert _is_transcribe_async_visit_path(FEISHU_URL) is False
    assert _is_transcribe_async_visit_path(ROOM_CODE) is False
    assert _is_transcribe_async_visit_path("pingcap/data/customer-uploads/a.docx") is False


def test_handle_dingtalk_transcribe_still_sync_full_chain_for_other_callers():
    """customer_document 等仍通过 process_document_url 走完整同步听记链路。"""
    svc = DocumentProcessingService()
    with patch.object(
        svc,
        "start_dingtalk_transcribe",
        return_value={
            "success": True,
            "notable_record_id": "n1",
            "transcribe_id": "t1",
        },
    ) as start, patch.object(
        svc,
        "poll_dingtalk_transcribe_summary",
        return_value={
            "success": True,
            "content": "summary",
            "document_type": "dingtalk_transcribe",
            "title": "钉钉听记总结_t1",
        },
    ) as poll:
        result = svc._handle_dingtalk_transcribe(TRANSCRIBE_URL)

    assert result["success"] is True
    assert result["content"] == "summary"
    start.assert_called_once_with(TRANSCRIBE_URL)
    poll.assert_called_once_with(notable_record_id="n1", transcribe_id="t1")


@patch("app.crm.save_engine.notify_aldebaran_visit_record_saved")
@patch("app.crm.save_engine.enrich_visit_record_with_document_content")
@patch("app.tasks.dingtalk_transcribe.update_visit_record_card_push_status")
@patch("app.tasks.dingtalk_transcribe.document_processing_service")
def test_celery_task_polls_only_not_write_table(
    mock_dps,
    _mock_status,
    mock_enrich,
    _mock_notify,
):
    """拜访听记 Celery 任务只轮询，不再次写 AI 表格。"""
    mock_dps.poll_dingtalk_transcribe_summary.return_value = {
        "success": True,
        "content": "听记总结正文",
        "document_type": "dingtalk_transcribe",
        "title": "钉钉听记总结_t1",
    }
    mock_enrich.return_value = 1

    task = process_dingtalk_transcribe_visit_record
    task.run = process_dingtalk_transcribe_visit_record.run  # bind=True 兼容

    with patch.object(
        process_dingtalk_transcribe_visit_record,
        "retry",
        side_effect=AssertionError("should not retry"),
    ):
        result = process_dingtalk_transcribe_visit_record(
            record_id="link_test_1",
            notable_record_id="n1",
            transcribe_id="t1",
            user_id="00000000-0000-0000-0000-000000000001",
            record_snapshot={
                "form_type": "simple",
                "visit_type": "link",
                "visit_url": TRANSCRIBE_URL,
            },
        )

    assert result["success"] is True
    mock_dps.poll_dingtalk_transcribe_summary.assert_called_once_with(
        notable_record_id="n1",
        transcribe_id="t1",
    )
    assert not getattr(mock_dps, "start_dingtalk_transcribe", MagicMock()).called
    mock_enrich.assert_called_once()


@patch("app.services.customer_document_service.DocumentProcessingService")
def test_customer_document_still_uses_process_document_url(mock_dps_cls):
    """客户文档上传未走拜访听记异步分支。"""
    from app.services.customer_document_service import CustomerDocumentService

    mock_dps = mock_dps_cls.return_value
    mock_dps.process_document_url.return_value = {"success": False, "message": "skip save"}

    svc = CustomerDocumentService()
    svc.upload_customer_document(
        db_session=MagicMock(),
        file_category="cat",
        account_name="ac",
        account_id="id",
        document_url=TRANSCRIBE_URL,
        uploader_id=UUID("00000000-0000-0000-0000-000000000001"),
        uploader_name="tester",
    )

    mock_dps.process_document_url.assert_called_once()
    call_kwargs = mock_dps.process_document_url.call_args.kwargs
    assert call_kwargs["document_url"] == TRANSCRIBE_URL

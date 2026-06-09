"""钉钉听记 link 拜访：轮询 AI 表格与文档 enrichment 异步任务。"""

import logging
from typing import Any, Dict
from uuid import UUID

from sqlmodel import Session

from app.celery import app as celery_app
from app.core.config import settings
from app.core.db import engine_transactional
from app.services.document_processing_service import document_processing_service
from app.tasks.visit_record_link_common import (
    mark_link_visit_enrichment_failed,
    parse_visit_record_snapshot,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    soft_time_limit=settings.CELERY_HEAVY_TASK_SOFT_TIME_LIMIT,
    time_limit=settings.CELERY_HEAVY_TASK_TIME_LIMIT,
)
def process_dingtalk_transcribe_visit_record(
    self,
    record_id: str,
    notable_record_id: str,
    transcribe_id: str,
    user_id: str,
    record_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """
    异步处理钉钉听记 link 拜访：轮询 AI 表格 → 文档 enrichment → 通知 Aldebaran 推卡。
    AI 表格写入已在 HTTP 请求中同步完成。
    """
    operator_user_id = UUID(user_id)
    record = parse_visit_record_snapshot(record_snapshot)

    from app.crm.save_engine import run_link_visit_enrichment_and_notify

    try:
        result = document_processing_service.poll_dingtalk_transcribe_summary(
            notable_record_id=notable_record_id,
            transcribe_id=transcribe_id,
        )
    except Exception as exc:
        logger.error(
            "听记总结轮询异常: record_id=%s, error=%s",
            record_id,
            exc,
            exc_info=True,
        )
        result = {"success": False, "message": "听记总结获取失败，请重试"}

    if not result.get("success"):
        mark_link_visit_enrichment_failed(record_id)
        return {
            "success": False,
            "record_id": record_id,
            "message": result.get("message", "听记处理失败"),
        }

    try:
        with Session(engine_transactional, expire_on_commit=False) as session:
            run_link_visit_enrichment_and_notify(
                record_id=record_id,
                record=record,
                record_snapshot=record_snapshot,
                operator_user_id=operator_user_id,
                db_session=session,
                content=result["content"],
                document_type=result["document_type"],
                title=result.get("title"),
            )
            session.commit()
    except Exception as exc:
        logger.error(
            "听记拜访记录 enrichment 失败: record_id=%s, error=%s",
            record_id,
            exc,
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=30, exc=exc)
        mark_link_visit_enrichment_failed(record_id)
        return {
            "success": False,
            "record_id": record_id,
            "message": "听记内容保存失败，请重试",
        }

    logger.info("钉钉听记拜访异步处理完成: record_id=%s", record_id)
    return {"success": True, "record_id": record_id}

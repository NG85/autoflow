"""非听记 link 拜访：LLM enrichment 与推卡异步任务。"""

import logging
from typing import Any, Dict
from uuid import UUID

from sqlmodel import Session

from app.celery import app as celery_app
from app.core.config import settings
from app.core.db import engine_transactional
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
def process_link_visit_enrichment(
    self,
    record_id: str,
    document_content_id: int,
    user_id: str,
    record_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """
    异步处理 link 拜访：对已落库的 raw content 做 LLM enrichment → 通知 Aldebaran 推卡。
    文档拉取与 raw content 写入已在 HTTP 请求中同步完成。
    """
    from app.crm.save_engine import run_link_visit_enrichment_and_notify

    operator_user_id = UUID(user_id)
    record = parse_visit_record_snapshot(record_snapshot)

    try:
        with Session(engine_transactional, expire_on_commit=False) as session:
            run_link_visit_enrichment_and_notify(
                record_id=record_id,
                record=record,
                record_snapshot=record_snapshot,
                operator_user_id=operator_user_id,
                db_session=session,
                document_content_id=document_content_id,
            )
            session.commit()
    except Exception as exc:
        logger.error(
            "link 拜访 enrichment 失败: record_id=%s, error=%s",
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
            "message": "拜访内容处理失败，请重试",
        }

    logger.info("link 拜访异步 enrichment 完成: record_id=%s", record_id)
    return {"success": True, "record_id": record_id}

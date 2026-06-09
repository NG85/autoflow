"""link 拜访 Celery 任务共用工具。"""

from __future__ import annotations

import logging
from typing import Any, Dict

from sqlmodel import Session

from app.api.routes.crm.models import CompleteVisitRecordCreate, SimpleVisitRecordCreate
from app.core.config import settings
from app.core.db import engine_transactional
from app.services.visit_record_card_push_status import (
    VisitRecordCardPushStatus,
    update_visit_record_card_push_status,
)

logger = logging.getLogger(__name__)


def parse_visit_record_snapshot(snapshot: Dict[str, Any]):
    form_type = snapshot.get("form_type") or settings.CRM_VISIT_RECORD_FORM_TYPE.value
    if form_type == "complete":
        return CompleteVisitRecordCreate.model_validate(snapshot)
    return SimpleVisitRecordCreate.model_validate(snapshot)


def mark_link_visit_enrichment_failed(record_id: str) -> None:
    with Session(engine_transactional, expire_on_commit=False) as session:
        update_visit_record_card_push_status(
            session,
            record_id,
            VisitRecordCardPushStatus.FAILED,
            commit=True,
        )

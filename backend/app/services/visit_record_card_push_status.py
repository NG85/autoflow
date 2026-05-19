"""拜访记录卡片推送状态（crm_sales_visit_records.card_push_status）。"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from app.models.crm_sales_visit_records import CRMSalesVisitRecord

logger = logging.getLogger(__name__)


class VisitRecordCardPushStatus:
    """卡片推送状态枚举值（存库为字符串）。"""

    PENDING = "pending"
    AWAITING_CALLBACK = "awaiting_callback"
    PUSHED = "pushed"
    FAILED = "failed"


def update_visit_record_card_push_status(
    session: Session,
    record_id: str,
    status: str,
    *,
    commit: bool = False,
) -> bool:
    """更新拜访记录的卡片推送状态；记录不存在时返回 False。"""
    row = session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    if not row:
        logger.warning("Cannot update card_push_status, record not found: %s", record_id)
        return False
    row.card_push_status = status
    session.add(row)
    if commit:
        session.commit()
    else:
        session.flush()
    logger.info("Updated card_push_status: record_id=%s status=%s", record_id, status)
    return True


def get_visit_record_card_push_status(session: Session, record_id: str) -> Optional[str]:
    row = session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    return row.card_push_status if row else None

"""拜访记录卡片推送状态（crm_sales_visit_records.card_push_status）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.services.visit_record_push_errors import (
    split_failed_recipients_by_retryable,
)

logger = logging.getLogger(__name__)


class VisitRecordCardPushStatus:
    """卡片推送状态枚举值（存库为字符串）。"""

    CONTENT_PROCESSING = "content_processing"
    PENDING = "pending"
    AWAITING_CALLBACK = "awaiting_callback"
    PUSHED = "pushed"
    PARTIAL_PUSHED = "partial_pushed"
    FAILED = "failed"


_PIPELINE_TERMINAL_STATUSES = frozenset({
    VisitRecordCardPushStatus.PUSHED,
    VisitRecordCardPushStatus.PARTIAL_PUSHED,
})


def is_terminal_card_push_status(status: Optional[str]) -> bool:
    """卡片推送链路已结束（含全量成功与部分成功），用于避免重复触发 Aldebaran。"""
    return status in _PIPELINE_TERMINAL_STATUSES


def should_skip_duplicate_card_push_callback(status: Optional[str]) -> bool:
    """全量推送成功后的重复回调可跳过；partial_pushed 允许定向重推失败接收者。"""
    return status == VisitRecordCardPushStatus.PUSHED


def resolve_card_push_status_from_notification_result(
    *,
    success_count: int,
    recipients_count: int = 0,
    failed_recipients: Optional[List[Any]] = None,
) -> str:
    """
    根据个人接收者推送结果解析卡片推送状态。
    - failed: 无人成功
    - partial_pushed: 部分人成功、部分人失败
    - pushed: 全部成功（无失败记录）
    """
    if success_count <= 0:
        return VisitRecordCardPushStatus.FAILED
    retryable_failed, skipped = split_failed_recipients_by_retryable(failed_recipients)
    if retryable_failed:
        return VisitRecordCardPushStatus.PARTIAL_PUSHED
    if recipients_count > 0 and success_count < recipients_count and not skipped:
        return VisitRecordCardPushStatus.PARTIAL_PUSHED
    return VisitRecordCardPushStatus.PUSHED


def resolve_card_push_status_after_retry(
    *,
    total_recipients: int,
    previously_failed_count: int,
    retry_success_count: int,
    retry_failed_recipients: Optional[List[Any]] = None,
) -> str:
    """合并首次部分成功与定向重推结果。"""
    retry_failed, _skipped = split_failed_recipients_by_retryable(retry_failed_recipients)
    total_success = max(0, total_recipients - previously_failed_count) + retry_success_count
    if total_success <= 0:
        return VisitRecordCardPushStatus.FAILED
    if retry_failed:
        return VisitRecordCardPushStatus.PARTIAL_PUSHED
    if total_recipients > 0 and total_success < total_recipients:
        return VisitRecordCardPushStatus.PARTIAL_PUSHED
    return VisitRecordCardPushStatus.PUSHED


def failed_recipients_to_recipients_by_platform(
    failed_recipients: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """将失败接收者快照还原为按平台分组的推送目标。"""
    recipients_by_platform: Dict[str, List[Dict[str, Any]]] = {}
    for item in failed_recipients or []:
        if not item.get("retryable", True):
            continue
        platform = (item.get("platform") or "").strip()
        open_id = (item.get("open_id") or "").strip()
        if not platform or not open_id:
            logger.warning(
                "Skip invalid card push retry recipient snapshot: platform=%s open_id=%s name=%s",
                platform,
                open_id,
                item.get("name"),
            )
            continue
        recipient = {
            "open_id": open_id,
            "name": item.get("name") or "Unknown",
            "type": item.get("type") or "leader",
            "receive_id_type": item.get("receive_id_type") or "open_id",
            "platform": platform,
        }
        recipients_by_platform.setdefault(platform, []).append(recipient)
    return recipients_by_platform


def link_content_status_from_card_push(card_push_status: Optional[str]) -> str:
    """
    供前端展示的 link 拜访内容/卡片处理状态。
    卡片本就走 Aldebaran 异步链路，pending/awaiting_callback 仍视为处理中。
    """
    if card_push_status == VisitRecordCardPushStatus.FAILED:
        return "failed"
    if card_push_status in {
        VisitRecordCardPushStatus.PUSHED,
        VisitRecordCardPushStatus.PARTIAL_PUSHED,
    }:
        return "completed"
    if card_push_status in {
        VisitRecordCardPushStatus.CONTENT_PROCESSING,
        VisitRecordCardPushStatus.PENDING,
        VisitRecordCardPushStatus.AWAITING_CALLBACK,
    }:
        return "processing"
    return "processing"


def update_visit_record_card_push_delivery(
    session: Session,
    record_id: str,
    status: str,
    *,
    failed_recipients: Optional[List[Dict[str, Any]]] = None,
    total_recipients: Optional[int] = None,
    commit: bool = False,
) -> bool:
    """更新卡片推送状态及失败接收者快照。"""
    row = session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    if not row:
        logger.warning("Cannot update card push delivery, record not found: %s", record_id)
        return False
    row.card_push_status = status
    if status == VisitRecordCardPushStatus.PUSHED:
        row.card_push_failed_recipients = None
        row.card_push_total_recipients = None
    elif status == VisitRecordCardPushStatus.PARTIAL_PUSHED:
        retryable_failed, _ = split_failed_recipients_by_retryable(failed_recipients)
        row.card_push_failed_recipients = list(retryable_failed)
        if total_recipients is not None:
            row.card_push_total_recipients = total_recipients
    elif status == VisitRecordCardPushStatus.FAILED:
        row.card_push_failed_recipients = list(failed_recipients or [])
        if total_recipients is not None:
            row.card_push_total_recipients = total_recipients
        else:
            row.card_push_total_recipients = None
    else:
        row.card_push_failed_recipients = None
        row.card_push_total_recipients = None
    session.add(row)
    if commit:
        session.commit()
    else:
        session.flush()
    logger.info(
        "Updated card push delivery: record_id=%s status=%s failed_count=%s total=%s",
        record_id,
        status,
        len(row.card_push_failed_recipients or []),
        row.card_push_total_recipients,
    )
    return True


def update_visit_record_card_push_status(
    session: Session,
    record_id: str,
    status: str,
    *,
    commit: bool = False,
) -> bool:
    """仅更新拜访记录的卡片推送状态（不改动失败快照）。"""
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


def get_visit_record_card_push_delivery(
    session: Session, record_id: str
) -> tuple[Optional[str], List[Dict[str, Any]], Optional[int]]:
    row = session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == record_id)
    ).first()
    if not row:
        return None, [], None
    failed = list(row.card_push_failed_recipients or [])
    return row.card_push_status, failed, row.card_push_total_recipients

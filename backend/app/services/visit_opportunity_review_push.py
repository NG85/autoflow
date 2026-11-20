"""拜访关联商机的评估 Markdown：接收人与拜访卡片同一套解析。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.config import settings
from app.crm.save_engine import (
    _crm_visit_record_row_to_push_dict,
    fill_sales_visit_record_fields,
)
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK, PLATFORM_WECOM
from app.services.platform_notification_service import platform_notification_service
from app.services.report_markdown_dispatch import _send_markdown_to_groups
from app.services.visit_record_push_policy import (
    filter_recipients_by_policy,
    load_visit_record_push_policy,
)

logger = logging.getLogger(__name__)

PUSH_TYPE = "visit_opportunity_review"


def handle_visit_opportunity_review_push(
    db_session: Session,
    *,
    record_id: str,
    content: str,
    title: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    report_id: Optional[str] = None,
    delivery: str = "card",
) -> Dict[str, Any]:
    """按拜访卡片的个人与部门群名单，推送商机评估 Markdown。"""
    normalized_record_id = (record_id or "").strip()
    if not normalized_record_id:
        raise HTTPException(status_code=422, detail="visit_opportunity_review requires record_id")
    message_text = (content or "").strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="visit_opportunity_review requires content")
    if not settings.VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED:
        logger.info(
            "visit opportunity review push disabled record_id=%s",
            normalized_record_id,
        )
        return {
            "success": True,
            "skipped": True,
            "reason": "feature_disabled",
            "type": PUSH_TYPE,
            "record_id": normalized_record_id,
            "opportunity_id": (opportunity_id or "").strip() or None,
            "report_id": (report_id or "").strip() or None,
            "message": "visit opportunity review push is disabled",
            "recipients_count": 0,
            "groups_count": 0,
            "success_count": 0,
            "failed_recipients": [],
        }

    row = db_session.exec(
        select(CRMSalesVisitRecord).where(CRMSalesVisitRecord.record_id == normalized_record_id)
    ).first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"visit record not found: {normalized_record_id}",
        )

    visit_record = _crm_visit_record_row_to_push_dict(row)
    visit_record = fill_sales_visit_record_fields(visit_record, db_session)
    recorder_id = visit_record.get("recorder_id")
    recorder_name = visit_record.get("recorder")
    if not recorder_id and not recorder_name:
        return _empty_result(
            record_id=normalized_record_id,
            message="No recorder found",
            opportunity_id=opportunity_id,
            report_id=report_id,
        )

    policy = load_visit_record_push_policy()
    recipients_by_platform, review_groups, brief_groups = (
        platform_notification_service._collect_visit_record_recipients_and_groups(
            db_session,
            recorder_name,
            recorder_id,
            visit_record,
            policy=policy,
        )
    )
    recipients_by_platform = filter_recipients_by_policy(recipients_by_platform, policy)
    groups = _merge_groups(
        review_groups if policy.group_enabled("review") else [],
        brief_groups if policy.group_enabled("brief") else [],
    )
    if not recipients_by_platform and not groups:
        return _empty_result(
            record_id=normalized_record_id,
            message="No recipients found",
            opportunity_id=opportunity_id,
            report_id=report_id,
        )

    label = (title or "").strip() or None
    delivery_mode = (delivery or "card").strip().lower()
    if delivery_mode not in {"card", "post"}:
        delivery_mode = "card"

    success_count, failed = _send_to_individuals(
        recipients_by_platform,
        content=message_text,
        title=label,
        delivery=delivery_mode,
    )
    group_success = 0
    if groups:
        group_success = _send_markdown_to_groups(
            platform_notification_service,
            groups,
            message_text,
            title=label,
            delivery=delivery_mode,
        )
    success_count += group_success

    individual_count = sum(len(items) for items in recipients_by_platform.values())
    return {
        "success": success_count > 0,
        "type": PUSH_TYPE,
        "record_id": normalized_record_id,
        "opportunity_id": (opportunity_id or "").strip() or None,
        "report_id": (report_id or "").strip() or None,
        "recipients_count": individual_count,
        "groups_count": len(groups),
        "success_count": success_count,
        "failed_recipients": failed,
    }


def _merge_groups(*group_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for groups in group_lists:
        for group in groups or []:
            key = (str(group.get("platform") or ""), str(group.get("chat_id") or ""))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            merged.append(group)
    return merged


def _send_to_individuals(
    recipients_by_platform: Dict[str, List[Dict[str, Any]]],
    *,
    content: str,
    title: Optional[str],
    delivery: str,
) -> Tuple[int, List[Dict[str, Any]]]:
    success_count = 0
    failed: List[Dict[str, Any]] = []
    tokens: Dict[str, str] = {}
    for platform, recipients in recipients_by_platform.items():
        for recipient in recipients:
            open_id = str(recipient.get("open_id") or "").strip()
            if not open_id:
                failed.append(
                    {
                        "name": recipient.get("name"),
                        "type": recipient.get("type"),
                        "platform": platform,
                        "message": "missing open_id",
                    }
                )
                continue
            try:
                _deliver_markdown(
                    platform=str(platform),
                    open_id=open_id,
                    content=content,
                    title=title,
                    delivery=delivery,
                    receive_id_type=str(recipient.get("receive_id_type") or "open_id"),
                    tokens=tokens,
                )
            except Exception as exc:
                logger.warning(
                    "visit opportunity review push failed platform=%s open_id=%s: %s",
                    platform,
                    open_id,
                    exc,
                    exc_info=True,
                )
                failed.append(
                    {
                        "open_id": open_id,
                        "name": recipient.get("name"),
                        "type": recipient.get("type"),
                        "platform": platform,
                        "message": str(exc),
                    }
                )
                continue
            success_count += 1
    return success_count, failed


def _deliver_markdown(
    *,
    platform: str,
    open_id: str,
    content: str,
    title: Optional[str],
    delivery: str,
    receive_id_type: str,
    tokens: Dict[str, str],
) -> None:
    service = platform_notification_service
    if platform == PLATFORM_WECOM and not _wecom_configured():
        raise RuntimeError("wecom push is not configured")
    if platform not in tokens:
        tokens[platform] = service._get_tenant_access_token(platform)
    token = tokens[platform]
    if platform in (PLATFORM_FEISHU, PLATFORM_LARK) and delivery == "post":
        payload = service.build_feishu_post_markdown(content, title=title)
        msg_type = "post"
    elif platform in (PLATFORM_FEISHU, PLATFORM_LARK):
        payload = service.build_feishu_markdown_card(content, title=title)
        msg_type = "interactive"
    elif platform == PLATFORM_WECOM:
        from app.platforms.wecom.text_notice import build_link_notice_from_markdown

        payload = build_link_notice_from_markdown(
            content,
            title=title or "",
            desc=(title or "商机评估")[:16],
            task_prefix="vor",
        )
        msg_type = "template_card"
    elif platform == PLATFORM_DINGTALK:
        payload = content
        msg_type = "text"
    else:
        raise RuntimeError(f"unsupported platform: {platform}")
    service._send_message(
        open_id,
        token,
        payload,
        platform,
        receive_id_type=receive_id_type,
        msg_type=msg_type,
        platform_tokens=tokens,
    )


def _wecom_configured() -> bool:
    from app.platforms.wecom.push_client import wecom_push_client

    return wecom_push_client.is_configured()


def _empty_result(
    *,
    record_id: str,
    message: str,
    opportunity_id: Optional[str],
    report_id: Optional[str],
) -> Dict[str, Any]:
    return {
        "success": False,
        "type": PUSH_TYPE,
        "record_id": record_id,
        "opportunity_id": (opportunity_id or "").strip() or None,
        "report_id": (report_id or "").strip() or None,
        "message": message,
        "recipients_count": 0,
        "groups_count": 0,
        "success_count": 0,
        "failed_recipients": [],
    }

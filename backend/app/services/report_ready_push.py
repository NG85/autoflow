"""visit_report 就绪：落库 crm_weekly_followup_summary 并按 report_push_policy 发送。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlmodel import Session

from app.models.crm_weekly_followup_summary import (
    CRMWeeklyFollowupSummary,
    REPORT_KIND_VISIT_REPORT,
)
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.platforms.notification_types import PERM_WEEKLY_REPORT_TEAM_RECEIVE
from app.repositories.department_mirror import department_mirror_repo
from app.services.crm_weekly_followup_service import crm_weekly_followup_service
from app.services.notification_delivery_preference import filter_opted_out_recipients
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_WEEKLY,
    VARIANT_VISIT_REPORT,
    normalize_scene,
)
from app.services.report_push_policy import load_report_push_policy

logger = logging.getLogger(__name__)


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    return date.fromisoformat(raw)


def _build_title(
    *,
    title: Optional[str],
    week_start: date,
    week_end: date,
    summary_type: str,
    department_name: str,
) -> str:
    explicit = str(title or "").strip()
    if explicit:
        return explicit
    week_part = f"{week_start.isoformat()}至{week_end.isoformat()}"
    if summary_type == "company":
        return f"周拜访报告-[{week_part}]"
    dept = department_name or "未知团队"
    return f"团队[{dept}]周拜访报告-[{week_part}]"


def handle_report_ready(
    db_session: Session,
    *,
    scene: str,
    week_start: date | str,
    week_end: date | str,
    content: str,
    variant: str = VARIANT_VISIT_REPORT,
    department_id: Optional[str] = None,
    department_name: Optional[str] = None,
    title: Optional[str] = None,
    delivery: str = "card",
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    scene_key = normalize_scene(scene)
    variant_key = str(variant or VARIANT_VISIT_REPORT).strip() or VARIANT_VISIT_REPORT
    if scene_key not in {SCENE_COMPANY_WEEKLY, SCENE_DEPARTMENT_WEEKLY}:
        raise ValueError("report_ready only supports company_weekly / department_weekly")
    if variant_key != VARIANT_VISIT_REPORT:
        raise ValueError("report_ready only supports variant=visit_report")

    content_text = str(content or "").strip()
    if not content_text:
        raise ValueError("content is required")

    start = _parse_date(week_start)
    end = _parse_date(week_end)
    dept_id = str(department_id or "").strip()
    dept_name = str(department_name or "").strip()
    summary_type = "company"
    if scene_key == SCENE_DEPARTMENT_WEEKLY:
        summary_type = "department"
        if dept_id and not dept_name:
            dept_name = department_mirror_repo.get_department_name_by_id(db_session, dept_id) or ""
        if dept_name and not dept_id:
            ids = department_mirror_repo.get_department_ids_by_name(db_session, dept_name)
            dept_id = ids[0] if ids else ""
        if not dept_name:
            raise ValueError("department_id or department_name is required for department_weekly")

    summary = CRMWeeklyFollowupSummary(
        week_start=start,
        week_end=end,
        summary_type=summary_type,
        department_id=dept_id,
        department_name=dept_name if summary_type == "department" else "",
        title=_build_title(
            title=title,
            week_start=start,
            week_end=end,
            summary_type=summary_type,
            department_name=dept_name,
        ),
        report_kind=REPORT_KIND_VISIT_REPORT,
        summary_content=content_text,
    )
    stored = crm_weekly_followup_service.upsert_summary(db_session, summary)

    policy = load_report_push_policy()
    if not policy.variant_enabled(scene_key, VARIANT_VISIT_REPORT):
        logger.info("visit_report stored but variant disabled: scene=%s", scene_key)
        return {
            "success": True,
            "stored": True,
            "sent": False,
            "skip_reason": "variant_disabled",
            "summary_id": str(stored.id),
            "recipients_count": 0,
            "success_count": 0,
        }

    override_ids = policy.override_user_ids(scene_key, VARIANT_VISIT_REPORT)
    if override_ids:
        recipients = platform_notification_service.recipients_from_user_ids(
            db_session, override_ids, recipient_type="variant_override"
        )
        groups: List[Dict[str, Any]] = []
    elif scene_key == SCENE_COMPANY_WEEKLY:
        recipients = platform_notification_service.get_recipients_for_company_weekly_report(
            db_session
        )
        groups = []
    else:
        recipients = platform_notification_service.resolve_department_report_recipients(
            db_session, dept_name
        )
        recipients = platform_notification_service._filter_recipients_by_receive_permission(
            db_session,
            recipients,
            PERM_WEEKLY_REPORT_TEAM_RECEIVE,
            report_kind="department weekly visit_report",
        )
        groups = platform_notification_service._get_group_chats_by_department(
            department_id=dept_id or None,
            department_name=dept_name,
            notification_type="department_review",
            db_session=db_session,
            review_scope="report",
        )

    dept_ids = [dept_id] if dept_id else department_mirror_repo.get_department_ids_by_name(
        db_session, dept_name
    )
    recipients = filter_opted_out_recipients(
        db_session,
        recipients,
        scene=scene_key,
        variant=VARIANT_VISIT_REPORT,
        department_ids=dept_ids or [""],
    )

    header = stored.title or title
    delivery_mode = (delivery or "card").strip().lower() or "card"
    success_count = 0
    failed: List[Dict[str, Any]] = []

    if groups:
        success_count += _send_visit_report_to_groups(
            platform_notification_service,
            groups,
            content_text,
            title=header,
            delivery=delivery_mode,
        )
    else:
        for recipient in recipients:
            uid = str(recipient.get("user_id") or recipient.get("userId") or "").strip()
            if not uid:
                failed.append({"recipient": recipient.get("name"), "message": "missing user_id"})
                continue
            try:
                result = platform_notification_service.send_platform_notification(
                    db_session,
                    recipient_user_id=uid,
                    content=content_text,
                    content_type="markdown",
                    title=header,
                    delivery=delivery_mode,
                )
            except Exception as exc:
                logger.warning("visit_report send failed user=%s: %s", uid, exc, exc_info=True)
                failed.append({"recipient_user_id": uid, "message": str(exc)})
                continue
            if result.get("success"):
                success_count += 1
            else:
                failed.append({"recipient_user_id": uid, "message": result.get("message")})

    return {
        "success": success_count > 0 or (not recipients and not groups),
        "stored": True,
        "sent": success_count > 0,
        "summary_id": str(stored.id),
        "scene": scene_key,
        "variant": VARIANT_VISIT_REPORT,
        "recipients_count": len(recipients) if not groups else len(groups),
        "success_count": success_count,
        "failed_recipients": failed,
        "sent_to_groups": bool(groups),
    }


def _send_visit_report_to_groups(
    service,
    groups: List[Dict[str, Any]],
    content: str,
    *,
    title: Optional[str],
    delivery: str,
) -> int:
    by_platform: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for group in groups:
        platform = str(group.get("platform") or "").strip()
        if platform:
            by_platform[platform].append(group)
    success = 0
    for platform, chats in by_platform.items():
        if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
            if delivery == "post":
                payload = service.build_feishu_post_markdown(content, title=title)
                msg_type = "post"
            else:
                payload = service.build_feishu_markdown_card(content, title=title)
                msg_type = "interactive"
        elif platform == PLATFORM_DINGTALK:
            payload = content
            msg_type = "text"
        else:
            continue
        success += service._send_content_to_group_chats(
            platform=platform,
            group_chats=chats,
            content=payload,
            msg_type=msg_type,
        )
    return success

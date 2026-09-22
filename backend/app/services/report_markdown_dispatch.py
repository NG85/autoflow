"""日/周拜访报告 Markdown 投递：解析部门、收件人，发飞书卡片或 post。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session

from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.platforms.notification_types import (
    PERM_DAILY_REPORT_TEAM_RECEIVE,
    PERM_WEEKLY_REPORT_TEAM_RECEIVE,
)
from app.repositories.department_mirror import department_mirror_repo
from app.services.notification_delivery_preference import filter_opted_out_recipients
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_DAILY,
    SCENE_COMPANY_WEEKLY,
    SCENE_DEPARTMENT_DAILY,
    SCENE_DEPARTMENT_WEEKLY,
    VARIANT_SUMMARY_MD,
)
from app.services.report_push_policy import load_report_push_policy

logger = logging.getLogger(__name__)

COMPANY_SCENES = frozenset({SCENE_COMPANY_DAILY, SCENE_COMPANY_WEEKLY})
DEPARTMENT_SCENES = frozenset({SCENE_DEPARTMENT_DAILY, SCENE_DEPARTMENT_WEEKLY})
DAILY_SCENES = frozenset({SCENE_COMPANY_DAILY, SCENE_DEPARTMENT_DAILY})
WEEKLY_SCENES = frozenset({SCENE_COMPANY_WEEKLY, SCENE_DEPARTMENT_WEEKLY})


def parse_iso_date(value: Any, *, field: str) -> date:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} is required")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc


def resolve_department(
    db_session: Session,
    *,
    department_id: Optional[str] = None,
    department_name: Optional[str] = None,
) -> Tuple[str, str]:
    dept_id = str(department_id or "").strip()
    dept_name = str(department_name or "").strip()
    if dept_id and not dept_name:
        dept_name = department_mirror_repo.get_department_name_by_id(db_session, dept_id) or ""
    if dept_name and not dept_id:
        ids = department_mirror_repo.get_department_ids_by_name(db_session, dept_name)
        dept_id = ids[0] if ids else ""
    return dept_id, dept_name


def department_ids_for_opt_out(
    db_session: Session,
    *,
    department_id: str,
    department_name: str,
) -> List[str]:
    if department_id:
        return [department_id]
    if department_name:
        return department_mirror_repo.get_department_ids_by_name(db_session, department_name) or [""]
    return [""]


def named_ids_are_eligibility(scene: str, variant: str) -> bool:
    """公司日报 summary_md：recipient_user_ids 即资格集；其余槽位上是覆盖名单。"""
    return scene == SCENE_COMPANY_DAILY and variant == VARIANT_SUMMARY_MD


def resolve_markdown_targets(
    db_session: Session,
    *,
    scene: str,
    variant: str,
    department_id: str = "",
    department_name: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    """返回 (recipients, groups, skip_reason)。groups 非空时只发群、不发个人。"""
    from app.services.platform_notification_service import platform_notification_service

    policy = load_report_push_policy()
    named_ids = policy.override_user_ids(scene, variant)
    groups: List[Dict[str, Any]] = []

    if named_ids_are_eligibility(scene, variant):
        if not named_ids:
            return [], [], "no_named_recipients"
        recipients = platform_notification_service.recipients_from_user_ids(
            db_session, named_ids, recipient_type="named_recipient"
        )
    elif named_ids:
        recipients = platform_notification_service.recipients_from_user_ids(
            db_session, named_ids, recipient_type="variant_override"
        )
    elif scene == SCENE_COMPANY_WEEKLY:
        recipients = platform_notification_service.get_recipients_for_company_weekly_report(
            db_session
        )
    elif scene == SCENE_COMPANY_DAILY:
        recipients = platform_notification_service.get_recipients_for_company_daily_report(
            db_session
        )
    elif scene in DEPARTMENT_SCENES:
        if not department_name:
            return [], [], "department_required"
        recipients = platform_notification_service.resolve_department_report_recipients(
            db_session, department_name
        )
        perm = (
            PERM_WEEKLY_REPORT_TEAM_RECEIVE
            if scene == SCENE_DEPARTMENT_WEEKLY
            else PERM_DAILY_REPORT_TEAM_RECEIVE
        )
        recipients = platform_notification_service._filter_recipients_by_receive_permission(
            db_session,
            recipients,
            perm,
            report_kind=f"{scene} {variant}",
        )
        groups = platform_notification_service._get_group_chats_by_department(
            department_id=department_id or None,
            department_name=department_name,
            notification_type="department_review",
            db_session=db_session,
            review_scope="report",
        )
    else:
        return [], [], "unsupported_scene"

    dept_ids = department_ids_for_opt_out(
        db_session, department_id=department_id, department_name=department_name
    )
    recipients = filter_opted_out_recipients(
        db_session,
        recipients,
        scene=scene,
        variant=variant,
        department_ids=dept_ids,
    )
    if not recipients and not groups:
        return [], [], "no_deliverable_recipients"
    return recipients, groups, None


def send_markdown_messages(
    db_session: Session,
    *,
    content: str,
    title: Optional[str],
    delivery: str,
    recipients: List[Dict[str, Any]],
    groups: List[Dict[str, Any]],
) -> Dict[str, Any]:
    from app.services.platform_notification_service import platform_notification_service

    delivery_mode = (delivery or "card").strip().lower() or "card"
    failed: List[Dict[str, Any]] = []
    if groups:
        success_count = _send_markdown_to_groups(
            platform_notification_service,
            groups,
            content,
            title=title,
            delivery=delivery_mode,
        )
        return {
            "success_count": success_count,
            "failed_recipients": failed,
            "sent_to_groups": True,
            "recipients_count": len(groups),
        }

    success_count = 0
    for recipient in recipients:
        uid = str(recipient.get("user_id") or recipient.get("userId") or "").strip()
        if not uid:
            failed.append({"recipient": recipient.get("name"), "message": "missing user_id"})
            continue
        try:
            result = platform_notification_service.send_platform_notification(
                db_session,
                recipient_user_id=uid,
                content=content,
                content_type="markdown",
                title=title,
                delivery=delivery_mode,
            )
        except Exception as exc:
            logger.warning("markdown report send failed user=%s: %s", uid, exc, exc_info=True)
            failed.append({"recipient_user_id": uid, "message": str(exc)})
            continue
        if result.get("success"):
            success_count += 1
        else:
            failed.append({"recipient_user_id": uid, "message": result.get("message")})
    return {
        "success_count": success_count,
        "failed_recipients": failed,
        "sent_to_groups": False,
        "recipients_count": len(recipients),
    }


def _send_markdown_to_groups(
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


def empty_push_result(
    *,
    scene: str,
    variant: str,
    skipped: bool = False,
    skip_reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = {
        "success": True,
        "skipped": skipped,
        "sent": False,
        "scene": scene,
        "variant": variant,
        "recipients_count": 0,
        "success_count": 0,
        "failed_recipients": [],
    }
    if skip_reason:
        result["skip_reason"] = skip_reason
    if extra:
        result.update(extra)
    return result

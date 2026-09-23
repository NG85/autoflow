"""周拜访报告 Markdown：读 crm_weekly_followup_summary（report_kind=visit_report）再推送。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from app.models.crm_weekly_followup_summary import (
    CRMWeeklyFollowupSummary,
    REPORT_KIND_VISIT_REPORT,
)
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_WEEKLY,
    VARIANT_VISIT_REPORT,
    normalize_scene,
)
from app.services.report_markdown_dispatch import (
    WEEKLY_SCENES,
    empty_push_result,
    parse_iso_date,
    resolve_department,
    resolve_markdown_targets,
    send_markdown_messages,
)
from app.services.report_push_policy import load_report_push_policy
from app.utils.crm_weekly_followup_week_boundary import resolve_weekly_followup_week_range
from app.utils.date_utils import beijing_today_date

logger = logging.getLogger(__name__)


def _resolve_week_range(
    week_start: Optional[date | str],
    week_end: Optional[date | str],
) -> tuple[date, date]:
    start_raw = week_start if isinstance(week_start, date) else str(week_start or "").strip()
    end_raw = week_end if isinstance(week_end, date) else str(week_end or "").strip()
    has_start = bool(start_raw)
    has_end = bool(end_raw)
    if has_start and has_end:
        start = week_start if isinstance(week_start, date) else parse_iso_date(week_start, field="week_start")
        end = week_end if isinstance(week_end, date) else parse_iso_date(week_end, field="week_end")
        return start, end
    if has_start or has_end:
        raise ValueError("week_start and week_end must be provided together")
    return resolve_weekly_followup_week_range(
        beijing_today_date(), week_range_mode="completed"
    )


def _default_title(
    *,
    title: Optional[str],
    week_start: date,
    week_end: date,
) -> str:
    explicit = str(title or "").strip()
    if explicit:
        return explicit
    week_part = f"{week_start.isoformat()}至{week_end.isoformat()}"
    return f"APTSell 销售经营周报｜{week_part}"


def _load_weekly_rows(
    db_session: Session,
    *,
    week_start: date,
    week_end: date,
    company: bool,
    department_id: str,
    department_name: str,
) -> List[CRMWeeklyFollowupSummary]:
    stmt = select(CRMWeeklyFollowupSummary).where(
        CRMWeeklyFollowupSummary.week_start == week_start,
        CRMWeeklyFollowupSummary.week_end == week_end,
        CRMWeeklyFollowupSummary.summary_type == ("company" if company else "department"),
        CRMWeeklyFollowupSummary.report_kind == REPORT_KIND_VISIT_REPORT,
    )
    if company:
        row = db_session.exec(stmt).first()
        return [row] if row else []
    if department_id:
        stmt = stmt.where(CRMWeeklyFollowupSummary.department_id == department_id)
    elif department_name:
        stmt = stmt.where(CRMWeeklyFollowupSummary.department_name == department_name)
    return list(db_session.exec(stmt).all())


def handle_report_ready(
    db_session: Session,
    *,
    scene: str,
    variant: str = VARIANT_VISIT_REPORT,
    week_start: Optional[date | str] = None,
    week_end: Optional[date | str] = None,
    department_id: Optional[str] = None,
    department_name: Optional[str] = None,
    title: Optional[str] = None,
    delivery: str = "card",
) -> Dict[str, Any]:
    scene_key = normalize_scene(scene)
    variant_key = str(variant or VARIANT_VISIT_REPORT).strip() or VARIANT_VISIT_REPORT
    if scene_key not in WEEKLY_SCENES:
        raise ValueError("weekly_visit_report only supports company_weekly / department_weekly")
    if variant_key != VARIANT_VISIT_REPORT:
        raise ValueError("weekly_visit_report only supports variant=visit_report")

    start, end = _resolve_week_range(week_start, week_end)
    extra = {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
    }
    base = empty_push_result(scene=scene_key, variant=variant_key, extra=extra)

    policy = load_report_push_policy()
    if not policy.variant_enabled(scene_key, VARIANT_VISIT_REPORT):
        logger.info("visit_report disabled: scene=%s", scene_key)
        return {**base, "skipped": True, "skip_reason": "variant_disabled"}

    company = scene_key == SCENE_COMPANY_WEEKLY
    dept_id, dept_name = ("", "")
    if not company:
        dept_id, dept_name = resolve_department(
            db_session, department_id=department_id, department_name=department_name
        )

    rows = _load_weekly_rows(
        db_session,
        week_start=start,
        week_end=end,
        company=company,
        department_id=dept_id,
        department_name=dept_name,
    )
    if not rows:
        logger.info(
            "weekly visit_report row missing: scene=%s week=%s..%s dept=%s",
            scene_key,
            start,
            end,
            dept_name or dept_id,
        )
        return {**base, "skipped": True, "skip_reason": "summary_not_found"}

    items: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    success_count = 0
    recipients_count = 0
    any_sent = False
    skip_reasons: List[str] = []

    for row in rows:
        row_dept_id = str(row.department_id or "").strip() or dept_id
        row_dept_name = str(row.department_name or "").strip() or dept_name
        content_text = str(row.summary_content or "").strip()
        item_extra = {
            "department_id": row_dept_id,
            "department_name": row_dept_name,
            "summary_id": str(row.id) if getattr(row, "id", None) else None,
        }
        if not content_text:
            skip_reasons.append("empty_content")
            items.append({**item_extra, "skipped": True, "skip_reason": "empty_content", "sent": False})
            continue

        recipients, groups, skip_reason = resolve_markdown_targets(
            db_session,
            scene=scene_key,
            variant=VARIANT_VISIT_REPORT,
            department_id=row_dept_id,
            department_name=row_dept_name,
        )
        if skip_reason:
            skip_reasons.append(skip_reason)
            items.append({**item_extra, "skipped": True, "skip_reason": skip_reason, "sent": False})
            continue

        header = _default_title(
            title=title,
            week_start=start,
            week_end=end,
        )
        sent = send_markdown_messages(
            db_session,
            content=content_text,
            title=header,
            delivery=delivery,
            recipients=recipients,
            groups=groups,
        )
        success_count += int(sent["success_count"] or 0)
        recipients_count += int(sent["recipients_count"] or 0)
        failed.extend(sent.get("failed_recipients") or [])
        sent_ok = int(sent["success_count"] or 0) > 0
        any_sent = any_sent or sent_ok
        items.append(
            {
                **item_extra,
                "skipped": False,
                "sent": sent_ok,
                "success_count": sent["success_count"],
                "sent_to_groups": sent["sent_to_groups"],
            }
        )

    if company:
        summary_id = items[0].get("summary_id") if items else None
        if not any_sent and skip_reasons:
            return {
                **base,
                "skipped": True,
                "skip_reason": skip_reasons[0],
                "summary_id": summary_id,
            }
        return {
            **base,
            "skipped": False,
            "sent": any_sent,
            "success": any_sent,
            "success_count": success_count,
            "recipients_count": recipients_count,
            "failed_recipients": failed,
            "summary_id": summary_id,
        }

    if not any_sent and skip_reasons and all(item.get("skipped") for item in items):
        return {
            **base,
            "skipped": True,
            "skip_reason": skip_reasons[0],
            "departments": items,
        }
    return {
        **base,
        "skipped": False,
        "sent": any_sent,
        "success": any_sent or not items,
        "success_count": success_count,
        "recipients_count": recipients_count,
        "failed_recipients": failed,
        "departments": items,
    }

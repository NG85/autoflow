"""日拜访报告 Markdown：读 crm_department_daily_summary.summary_content 再推送。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from app.models.crm_department_daily_summary import CRMDepartmentDailySummary
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_DAILY,
    VARIANT_SUMMARY_MD,
    normalize_scene,
)
from app.services.report_markdown_dispatch import (
    DAILY_SCENES,
    department_names_for_report_push,
    empty_push_result,
    no_record_message,
    resolve_department,
    resolve_markdown_targets,
    send_markdown_messages,
)
from app.services.report_push_policy import load_report_push_policy
from app.utils.date_utils import beijing_today_date

logger = logging.getLogger(__name__)


def _parse_report_date(value: Any) -> date:
    if value is None or str(value).strip() == "":
        return beijing_today_date() - timedelta(days=1)
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def _default_title(*, report_date: date, title: Optional[str]) -> str:
    explicit = str(title or "").strip()
    if explicit:
        return explicit
    return f"APTSell 销售经营日报｜{report_date.isoformat()}"


def _load_daily_rows(
    db_session: Session,
    *,
    target: date,
    company: bool,
    department_id: str,
    department_name: str,
) -> List[CRMDepartmentDailySummary]:
    stmt = select(CRMDepartmentDailySummary).where(
        CRMDepartmentDailySummary.report_date == target,
        CRMDepartmentDailySummary.summary_type == ("company" if company else "department"),
    )
    if not company:
        if department_id:
            stmt = stmt.where(CRMDepartmentDailySummary.department_id == department_id)
        elif department_name:
            stmt = stmt.where(CRMDepartmentDailySummary.department_name == department_name)
    rows = list(db_session.exec(stmt).all())
    if company:
        return rows[:1]

    by_key: Dict[tuple[str, str], CRMDepartmentDailySummary] = {}
    for row in rows:
        key = (str(row.department_id or "").strip(), str(row.department_name or "").strip())
        content = str(row.summary_content or "").strip()
        existing = by_key.get(key)
        if existing is None or (content and not str(existing.summary_content or "").strip()):
            by_key[key] = row
    return list(by_key.values())


def handle_daily_summary(
    db_session: Session,
    *,
    scene: str = SCENE_COMPANY_DAILY,
    variant: str = VARIANT_SUMMARY_MD,
    report_date: Optional[date | str] = None,
    title: Optional[str] = None,
    delivery: str = "card",
    department_id: Optional[str] = None,
    department_name: Optional[str] = None,
) -> Dict[str, Any]:
    scene_key = normalize_scene(scene)
    variant_key = str(variant or VARIANT_SUMMARY_MD).strip() or VARIANT_SUMMARY_MD
    if scene_key not in DAILY_SCENES:
        raise ValueError("daily_visit_report only supports scene=company_daily / department_daily")
    if variant_key != VARIANT_SUMMARY_MD:
        raise ValueError("daily_visit_report only supports variant=summary_md")

    target = _parse_report_date(report_date)
    extra = {"report_date": target.isoformat()}
    base = empty_push_result(scene=scene_key, variant=variant_key, extra=extra)

    policy = load_report_push_policy()
    if not policy.variant_enabled(scene_key, VARIANT_SUMMARY_MD):
        logger.info("daily summary_md disabled: scene=%s", scene_key)
        return {**base, "skipped": True, "skip_reason": "variant_disabled"}

    company = scene_key == SCENE_COMPANY_DAILY
    dept_id, dept_name = ("", "")
    allowed_names: set[str] = set()
    if not company:
        dept_id, dept_name = resolve_department(
            db_session, department_id=department_id, department_name=department_name
        )
        allowed_names = department_names_for_report_push(db_session, weekly=False)
        if (dept_id or dept_name) and dept_name not in allowed_names:
            return {**base, "skipped": True, "skip_reason": "department_not_in_scope"}

    rows = _load_daily_rows(
        db_session,
        target=target,
        company=company,
        department_id=dept_id,
        department_name=dept_name,
    )
    if not company:
        scoped_rows = [
            row
            for row in rows
            if str(row.department_name or "").strip() in allowed_names
        ]
        rows = list(scoped_rows)
        covered = {str(row.department_name or "").strip() for row in rows}
        target_names = [dept_name] if dept_name else sorted(allowed_names)
        for name in target_names:
            if name and name not in covered:
                rows.append(
                    SimpleNamespace(
                        department_id=dept_id if name == dept_name else "",
                        department_name=name,
                        summary_content="",
                        summary_missing=True,
                    )
                )
        if not rows:
            return {**base, "skipped": True, "skip_reason": "no_departments"}
    elif not rows:
        rows = [
            SimpleNamespace(
                department_id="",
                department_name="",
                summary_content="",
                summary_missing=True,
            )
        ]

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
        summary_missing = getattr(row, "summary_missing", False) is True
        item_extra = {
            "department_id": row_dept_id,
            "department_name": row_dept_name,
        }
        placeholder = not content_text
        if placeholder:
            content_text = no_record_message(
                weekly=False,
                report_date=target,
                department_name="" if company else row_dept_name,
                summary_missing=summary_missing,
            )

        recipients, groups, skip_reason = resolve_markdown_targets(
            db_session,
            scene=scene_key,
            variant=VARIANT_SUMMARY_MD,
            department_id=row_dept_id,
            department_name=row_dept_name,
        )
        if skip_reason:
            skip_reasons.append(skip_reason)
            items.append({**item_extra, "skipped": True, "skip_reason": skip_reason, "sent": False})
            continue

        header = _default_title(
            report_date=target,
            title=title,
        )
        sent = send_markdown_messages(
            db_session,
            content=content_text,
            title=header,
            delivery=delivery,
            recipients=recipients,
            groups=groups,
            ops_cc_source=None if placeholder else ("company daily report" if company else None),
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
        if not any_sent and skip_reasons:
            return {**base, "skipped": True, "skip_reason": skip_reasons[0]}
        return {
            **base,
            "skipped": False,
            "sent": any_sent,
            "success": any_sent,
            "success_count": success_count,
            "recipients_count": recipients_count,
            "failed_recipients": failed,
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

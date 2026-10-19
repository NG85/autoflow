"""一线销售本周拜访：只统计当前登录人自己的拜访和销售视角复盘。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models.crm_entity_insight import CRMEntityInsight
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.services.visit_record_insight_reader import (
    VISIT_INSIGHT_ENTITY_TYPE,
    VISIT_INSIGHT_SALES_TYPE,
)
from app.utils.date_utils import get_week_range_from_date

logger = logging.getLogger(__name__)

SEVERITY_ORDER = ("HIGH", "MEDIUM", "LOW")


def empty_week_visit_summary(week_start: date, week_end: date) -> dict[str, Any]:
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "visit_total": 0,
        "recap_total": 0,
        "recap_by_severity": [{"severity": key, "count": 0} for key in SEVERITY_ORDER],
    }


def summarize_week_visit_recaps(
    record_ids: Sequence[str],
    insight_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """同一拜访只计最新一条销售视角复盘，再按 severity 计数。"""
    wanted = {_text(item) for item in record_ids if _text(item)}
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for row in insight_rows or []:
        entity_id = _text(_row_value(row, "entity_id"))
        if not entity_id or entity_id not in wanted or entity_id in seen:
            continue
        seen.add(entity_id)
        severity = _text(_row_value(row, "severity")).upper() or "UNKNOWN"
        counts[severity] = counts.get(severity, 0) + 1
    groups = [{"severity": key, "count": counts.pop(key, 0)} for key in SEVERITY_ORDER]
    for key in sorted(counts):
        groups.append({"severity": key, "count": counts[key]})
    return {
        "visit_total": len(list(record_ids)),
        "recap_total": len(seen),
        "recap_by_severity": groups,
    }


def load_week_visit_summary(
    session: Session,
    recorder_user_id: Any,
    report_date: date,
) -> dict[str, Any]:
    """按拜访日期统计本人本周拜访总数，以及销售视角复盘的 severity 分布。"""
    week_start, week_end = get_week_range_from_date(report_date)
    payload = empty_week_visit_summary(week_start, week_end)
    recorder_id = _text(recorder_user_id)
    if not recorder_id:
        return payload
    try:
        visit_rows = session.exec(
            select(CRMSalesVisitRecord.record_id).where(
                CRMSalesVisitRecord.recorder_id == recorder_user_id,
                CRMSalesVisitRecord.visit_communication_date >= week_start,
                CRMSalesVisitRecord.visit_communication_date <= week_end,
            )
        ).all()
    except Exception as exc:
        logger.warning("load week visits failed recorder=%s: %s", recorder_id, exc)
        return payload
    record_ids = [_text(row) for row in visit_rows]
    record_ids = [item for item in record_ids if item]
    insight_rows: list[Any] = []
    if record_ids:
        try:
            insight_rows = list(
                session.exec(
                    select(CRMEntityInsight)
                    .where(CRMEntityInsight.entity_type == VISIT_INSIGHT_ENTITY_TYPE)
                    .where(CRMEntityInsight.entity_id.in_(record_ids))
                    .where(CRMEntityInsight.insight_type == VISIT_INSIGHT_SALES_TYPE)
                    .where(CRMEntityInsight.is_deleted == 0)
                    .where(
                        or_(
                            CRMEntityInsight.expires_at.is_(None),
                            CRMEntityInsight.expires_at > func.utc_timestamp(),
                        )
                    )
                    .order_by(col(CRMEntityInsight.generated_at).desc(), col(CRMEntityInsight.id).desc())
                ).all()
            )
        except Exception as exc:
            logger.warning("load week visit recaps failed recorder=%s: %s", recorder_id, exc)
    summary = summarize_week_visit_recaps(record_ids, insight_rows)
    payload.update(summary)
    payload["visit_total"] = len(visit_rows)
    return payload


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)

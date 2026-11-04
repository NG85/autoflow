"""负责人今日重点上的报告入口：重点商机、团队人效、经营趋势。

只读 crm_entity_insight。查不到或筛不到就不写进卡片，不挡住需介入推送。
入口都打开今日重点页 /v2/department-highlights，tab 为槽位 key。

槽位筛选（entity_type / insight_type / period_key）集中在 LEADER_REPORT_SLOTS。
和需介入抽取同一套人：entity_type=USER，entity_id 是负责人 oauth id。
insight_types 为空的槽位暂不出现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models.crm_entity_insight import CRMEntityInsight
from app.utils.push_page_urls import build_department_highlights_url

logger = logging.getLogger(__name__)

ENTITY_USER = "USER"

# 团队人效、经营趋势周一/周五展示，也是当天任务写出、当天用进卡片。
# period_key 存执行日 YYYY-MM-DD，取今日重点时按 report_date 匹配。
# 卡片文案不带上周/本周。
INSIGHT_KEY_OPPORTUNITIES: tuple[str, ...] = ("KEY_DEAL_REVIEW",)
INSIGHT_PERSONA: tuple[str, ...] = ("LEADER_EFFICIENCY_INSIGHT",)
INSIGHT_BUSINESS_TREND: tuple[str, ...] = ("TEAM_OPERATING_TREND",)


@dataclass(frozen=True)
class LeaderReportSlot:
    key: str
    title: str
    entity_type: str
    insight_types: tuple[str, ...]
    match_period: bool = True


LEADER_REPORT_SLOTS: tuple[LeaderReportSlot, ...] = (
    LeaderReportSlot(
        key="key_deal_review",
        title="重点商机",
        entity_type=ENTITY_USER,
        insight_types=INSIGHT_KEY_OPPORTUNITIES,
    ),
    LeaderReportSlot(
        key="leader_efficiency_insight",
        title="团队人效",
        entity_type=ENTITY_USER,
        insight_types=INSIGHT_PERSONA,
    ),
    LeaderReportSlot(
        key="team_operating_trend",
        title="经营趋势",
        entity_type=ENTITY_USER,
        insight_types=INSIGHT_BUSINESS_TREND,
    ),
)


def report_period(report_date: date) -> str:
    """period_key 对执行日 YYYY-MM-DD。"""
    return report_date.isoformat()


def report_entry_lines(entries: Optional[Sequence[Mapping[str, Any]]]) -> list[str]:
    """三个入口写成一行蓝链。没有匹配到的槽位不出现。"""
    links = [
        f"[{_text(item.get('label'))}]({_text(item.get('url'))})"
        for item in (entries or [])
        if _text(item.get("label")) and _text(item.get("url"))
    ]
    return ["  ".join(links)] if links else []


def pick_report_entries(
    rows: Sequence[Any],
    *,
    period_key: str,
    slots: Sequence[LeaderReportSlot] = LEADER_REPORT_SLOTS,
) -> list[dict[str, str]]:
    """每个槽位取最新一条能拼出今日重点页 URL 的洞察。"""
    return [
        {"key": section["key"], "label": section["title"], "url": url}
        for section in pick_report_sections(rows, period_key=period_key, slots=slots)
        if (url := _text(section.get("url")))
    ]


def pick_report_sections(
    rows: Sequence[Any],
    *,
    period_key: str,
    slots: Sequence[LeaderReportSlot] = LEADER_REPORT_SLOTS,
) -> list[dict[str, Any]]:
    """每个槽位取最新一条洞察，组成列表页的 section。"""
    sections: list[dict[str, Any]] = []
    for slot in slots:
        if not slot.insight_types:
            continue
        row = _latest_for_slot(rows, slot, period_key)
        if row is None:
            continue
        item = _serialize_report_item(row)
        if not item.get("unique_id"):
            continue
        if not item.get("summary") and not item.get("detail_text"):
            continue
        sections.append(
            {
                "key": slot.key,
                "title": slot.title,
                "item_type": _text(_row_value(row, "insight_type")) or slot.key,
                "items": [item],
                "url": _entry_url(slot, period_key),
            }
        )
    return sections


def load_leader_insight_reports(
    session: Session,
    oauth_user_id: str,
    report_date: date,
    *,
    slots: Sequence[LeaderReportSlot] = LEADER_REPORT_SLOTS,
) -> list[dict[str, str]]:
    """按负责人 oauth id 取报告入口。查库失败返回空列表。"""
    rows, period_key, active = _active_rows(session, oauth_user_id, report_date, slots)
    if not rows or not active:
        return []
    return pick_report_entries(rows, period_key=period_key, slots=active)


def load_leader_insight_sections(
    session: Session,
    oauth_user_id: str,
    report_date: date,
    *,
    slots: Sequence[LeaderReportSlot] = LEADER_REPORT_SLOTS,
) -> list[dict[str, Any]]:
    """按负责人 oauth id 取报告正文。查库失败返回空列表。"""
    rows, period_key, active = _active_rows(session, oauth_user_id, report_date, slots)
    if not rows or not active:
        return []
    return pick_report_sections(rows, period_key=period_key, slots=active)


def _active_rows(
    session: Session,
    oauth_user_id: str,
    report_date: date,
    slots: Sequence[LeaderReportSlot],
) -> tuple[list[Any], str, list[LeaderReportSlot]]:
    active = [slot for slot in slots if slot.insight_types]
    entity_id = _text(oauth_user_id)
    if not active or not entity_id:
        return [], "", active
    period_key = report_period(report_date)
    try:
        return _load_rows(session, entity_id, active), period_key, active
    except Exception:
        logger.exception("leader insight reports load failed")
        return [], period_key, active


def _serialize_report_item(row: Any) -> dict[str, Any]:
    """和拜访复盘完整报告一样，只给页面正文：摘要和全文。"""
    return {
        "unique_id": _text(_row_value(row, "unique_id")) or None,
        "summary": _text(_row_value(row, "summary")) or None,
        "detail_text": _text(_row_value(row, "detail_text")) or None,
    }


def _load_rows(
    session: Session,
    oauth_user_id: str,
    slots: Sequence[LeaderReportSlot],
) -> list[Any]:
    types = list(dict.fromkeys(item for slot in slots for item in slot.insight_types))
    entity_types = list(dict.fromkeys(slot.entity_type for slot in slots if slot.entity_type))
    if not types or not oauth_user_id:
        return []
    statement = (
        select(CRMEntityInsight)
        .where(CRMEntityInsight.entity_id == oauth_user_id)
        .where(CRMEntityInsight.insight_type.in_(types))
        .where(CRMEntityInsight.is_deleted == 0)
        .where(
            or_(
                CRMEntityInsight.expires_at.is_(None),
                CRMEntityInsight.expires_at > func.utc_timestamp(),
            )
        )
        .order_by(col(CRMEntityInsight.generated_at).desc(), col(CRMEntityInsight.id).desc())
    )
    if entity_types:
        statement = statement.where(CRMEntityInsight.entity_type.in_(entity_types))
    return list(session.exec(statement).all())


def _latest_for_slot(rows: Sequence[Any], slot: LeaderReportSlot, period_key: str) -> Any:
    wanted = {item.upper() for item in slot.insight_types}
    entity_type = slot.entity_type.upper()
    for row in rows:
        if _text(_row_value(row, "entity_type")).upper() != entity_type:
            continue
        if _text(_row_value(row, "insight_type")).upper() not in wanted:
            continue
        if slot.match_period and _text(_row_value(row, "period_key")) != period_key:
            continue
        return row
    return None


def _entry_url(slot: LeaderReportSlot, period_key: str) -> str:
    return build_department_highlights_url(period_key, "", slot.key)


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

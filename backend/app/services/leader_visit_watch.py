"""负责人今日重点在周一、周五附带的团队拜访对照。

周一对照上一周（上周一到上周日），周五对照本周（本周一到本周日）。
先写「{部门名}总拜访 N 条」，再点名。口径和一线「本周已拜访」一致：按录入人、拜访沟通日落在该周，
复盘取每条拜访最新一条销售视角复盘。卡片点名只用 severity=HIGH（需关注）。

- 拜访偏少：团队里有人录入时，这一周一条都没录入的成员。全员都是 0 时不点名。
  负责人本人计入团队总数，不参与点名。
- 需关注偏多：销售视角复盘 severity=HIGH 至少 2 条，并且不少于团队人均的两倍。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models.crm_entity_insight import CRMEntityInsight
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.user_profile import UserProfile
from app.services.sales_week_visits import summarize_week_visit_recaps
from app.services.visit_record_insight_reader import (
    VISIT_INSIGHT_ENTITY_TYPE,
    VISIT_INSIGHT_SALES_TYPE,
)
from app.utils.date_utils import get_week_range_from_date
from app.utils.push_page_urls import build_week_visit_list_url

logger = logging.getLogger(__name__)

LABEL_LAST_WEEK = "上周"
LABEL_THIS_WEEK = "本周"
LOW_ENTRY_LABEL = "拜访偏少"
HIGH_HEAVY_LABEL = "需关注偏多"


def visit_watch_window(report_date: date) -> Optional[tuple[date, date, str]]:
    """周一返回上一周，周五返回本周。其他日子不展示。"""
    if report_date.weekday() == 0:
        start, end = get_week_range_from_date(report_date - timedelta(days=7))
        return start, end, LABEL_LAST_WEEK
    if report_date.weekday() == 4:
        start, end = get_week_range_from_date(report_date)
        return start, end, LABEL_THIS_WEEK
    return None


def select_visit_watch(members: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """从成员的拜访数和高严重度条数里挑出要写进卡片的人。"""
    rows = [member for member in members if _text(member.get("name"))]
    if not rows:
        return {"low_entry": [], "high_severity": []}
    visit_average = sum(int(member.get("visit_total") or 0) for member in rows) / len(rows)
    high_average = sum(int(member.get("high_count") or 0) for member in rows) / len(rows)
    low_entry = [
        {"name": _text(member.get("name")), "visit_total": int(member.get("visit_total") or 0)}
        for member in rows
        if _is_low_entry(int(member.get("visit_total") or 0), visit_average)
    ]
    high_severity = [
        {"name": _text(member.get("name")), "high_count": int(member.get("high_count") or 0)}
        for member in rows
        if _is_high_heavy(int(member.get("high_count") or 0), high_average)
    ]
    low_entry.sort(key=lambda item: (item["visit_total"], item["name"]))
    high_severity.sort(key=lambda item: (-item["high_count"], item["name"]))
    return {"low_entry": low_entry, "high_severity": high_severity}


def visit_watch_lines(
    watch: Optional[Mapping[str, Any]],
    *,
    dingtalk: bool = False,
    include_detail: bool = True,
) -> list[str]:
    """先写团队总数和复盘分级，再点名。没有统计、也没有要点名的人时不加这一段。"""
    if not watch:
        return []
    low_entry = list(watch.get("low_entry") or [])
    high_severity = list(watch.get("high_severity") or [])
    has_stats = "visit_total" in watch
    if not has_stats and not low_entry and not high_severity:
        return []
    label = _text(watch.get("label")) or LABEL_THIS_WEEK
    heading = f"## {label}拜访" if dingtalk else f"**{label}拜访**"
    lines = [heading]
    if has_stats:
        lines.append(_stats_line(watch))
    if low_entry:
        lines.append(f"{LOW_ENTRY_LABEL}：" + "、".join(f"{item['name']} {item['visit_total']} 条" for item in low_entry))
    if high_severity:
        lines.append(f"{HIGH_HEAVY_LABEL}：" + "、".join(f"{item['name']} {item['high_count']} 条" for item in high_severity))
    if include_detail:
        detail = _detail_link(watch.get("url"), label)
        if detail:
            lines.append(detail)
    return lines


def load_leader_visit_watch(
    session: Session,
    department_names: Sequence[str],
    report_date: date,
    *,
    leader_user_id: Any = None,
) -> Optional[dict[str, Any]]:
    """按负责人卡片上的部门取在职成员并对照。查库失败时返回 None，不挡住需介入卡片。"""
    window = visit_watch_window(report_date)
    if window is None:
        return None
    week_start, week_end, label = window
    try:
        members, summary = _member_counts(
            session,
            department_names,
            week_start,
            week_end,
            exclude_user_id=leader_user_id,
        )
    except Exception:
        logger.exception("leader visit watch failed")
        return None
    if int(summary.get("member_count") or 0) <= 0:
        return None
    names = list(dict.fromkeys(_text(name) for name in department_names if _text(name)))
    picked = select_visit_watch(members)
    return {
        "label": label,
        "department_names": names,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "visit_total": int(summary.get("visit_total") or 0),
        "recap_total": int(summary.get("recap_total") or 0),
        "recap_by_severity": list(summary.get("recap_by_severity") or []),
        "url": build_week_visit_list_url(week_start.isoformat(), week_end.isoformat(), names),
        **picked,
    }


def _member_counts(
    session: Session,
    department_names: Sequence[str],
    week_start: date,
    week_end: date,
    *,
    exclude_user_id: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """成员拜访数，以及与一线本周拜访相同口径的团队汇总。"""
    empty = summarize_week_visit_recaps([], [])
    names = list(dict.fromkeys(_text(name) for name in department_names if _text(name)))
    if not names:
        return [], empty
    profiles = session.exec(
        select(UserProfile).where(
            UserProfile.department.in_(names),
            UserProfile.is_active == True,  # noqa: E712
            UserProfile.user_id.is_not(None),
        )
    ).all()
    skip = _text(exclude_user_id)
    by_user: dict[str, str] = {}
    leaders: set[str] = set()
    for profile in profiles:
        user_id = str(profile.user_id or "").strip()
        oauth_id = _text(getattr(profile, "oauth_user_id", None))
        name = _text(profile.name)
        if not user_id or not name or user_id in by_user:
            continue
        by_user[user_id] = name
        if skip and skip in {user_id, oauth_id}:
            leaders.add(user_id)
    if not by_user:
        return [], empty
    recorder_ids = _uuids(by_user)
    if not recorder_ids:
        return [], empty
    visits = session.exec(
        select(CRMSalesVisitRecord.recorder_id, CRMSalesVisitRecord.record_id).where(
            CRMSalesVisitRecord.recorder_id.in_(recorder_ids),
            CRMSalesVisitRecord.visit_communication_date >= week_start,
            CRMSalesVisitRecord.visit_communication_date <= week_end,
        )
    ).all()
    record_owner: dict[str, str] = {}
    record_ids: list[str] = []
    visit_totals: dict[str, int] = defaultdict(int)
    for recorder_id, record_id in visits:
        owner = str(recorder_id or "").strip()
        if not owner or owner not in by_user:
            continue
        visit_totals[owner] += 1
        record = _text(record_id)
        if not record:
            continue
        record_ids.append(record)
        record_owner[record] = owner
    insight_rows = _sales_view_insights(session, record_ids)
    summary = summarize_week_visit_recaps(record_ids, insight_rows)
    summary["visit_total"] = sum(visit_totals.values())
    summary["member_count"] = len(by_user)
    high_counts = _high_counts(insight_rows, record_owner)
    members = [
        {
            "name": name,
            "visit_total": visit_totals.get(user_id, 0),
            "high_count": high_counts.get(user_id, 0),
        }
        for user_id, name in by_user.items()
        if user_id not in leaders
    ]
    return members, summary


def _sales_view_insights(session: Session, record_ids: Sequence[str]) -> list[CRMEntityInsight]:
    """与一线本周拜访相同：未删除、未过期的销售视角复盘，新的在前。"""
    ids = [item for item in record_ids if item]
    if not ids:
        return []
    return list(
        session.exec(
            select(CRMEntityInsight)
            .where(CRMEntityInsight.entity_type == VISIT_INSIGHT_ENTITY_TYPE)
            .where(CRMEntityInsight.entity_id.in_(ids))
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


def _high_counts(insight_rows: Sequence[Any], record_owner: Mapping[str, str]) -> dict[str, int]:
    """每条拜访只看最新一条销售视角复盘，severity=HIGH 记到录入人名下。"""
    seen: set[str] = set()
    counts: dict[str, int] = defaultdict(int)
    for row in insight_rows:
        entity_id = _text(getattr(row, "entity_id", None))
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        if _text(getattr(row, "severity", None)).upper() != "HIGH":
            continue
        owner = record_owner.get(entity_id)
        if owner:
            counts[owner] += 1
    return dict(counts)


def _detail_link(url: Any, label: str) -> str:
    target = _text(url)
    if not target:
        return ""
    return f"[查看{label}拜访]({target})"


def _team_phrase(names: Sequence[str]) -> str:
    """用部门原名。多个部门用顿号接上。"""
    cleaned = list(dict.fromkeys(_text(name) for name in names if _text(name)))
    return "、".join(cleaned)


def _stats_line(watch: Mapping[str, Any]) -> str:
    total = int(watch.get("visit_total") or 0)
    team = _team_phrase(watch.get("department_names") or [])
    if not team:
        return f"总拜访 {total} 条"
    return f"{team}总拜访 {total} 条"


def _is_low_entry(visit_total: int, average: float) -> bool:
    return average > 0 and visit_total <= 0


def _is_high_heavy(high_count: int, average: float) -> bool:
    return high_count >= 2 and high_count >= average * 2


def _uuids(by_user: Mapping[str, str]) -> list[UUID]:
    parsed: list[UUID] = []
    for raw in by_user:
        try:
            parsed.append(UUID(raw))
        except (ValueError, TypeError):
            continue
    return parsed


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

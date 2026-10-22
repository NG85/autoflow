"""一线销售今日重点：只读 crm_skill_extract_items，组装轻量卡。

只消费 subject_type=USER、audience=sales。部门 / 公司今日重点不在这里。
入口标题用抽取行上的 card_links 快照，不另维护一份标题表。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlmodel import Session, select

from app.models.crm_accounts import CRMAccount
from app.models.crm_opportunities import CRMOpportunity
from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.services.notification_delivery_preference import filter_opted_out_recipients
from app.services.notification_scene_catalog import (
    SCENE_SALES_DAILY,
    VARIANT_TODAY_HIGHLIGHTS,
)
from app.services.report_push_policy import load_report_push_policy
from app.utils.date_utils import beijing_today_date, get_week_range_from_date
from app.utils.push_page_urls import (
    build_sales_today_highlights_url,
    build_task_list_page_url,
    build_visit_list_page_url,
)

logger = logging.getLogger(__name__)

SUBJECT_USER = "USER"
AUDIENCE_SALES = "sales"
STATUS_SUPERSEDED = "SUPERSEDED"
_DECIDED = {"ADOPTED", "REJECTED"}
ITEM_IMMEDIATE = "IMMEDIATE_ACTION"
ITEM_RISK = "OPEN_RISK"
ITEM_NEXT_WEEK = "NEXT_WEEK_ACTION_CANDIDATE"
FRIDAY = 4
WEEK_TASK_LINK_LABEL = "本周待完成"
WEEK_FOLLOWUP_LINK_LABEL = "本周已拜访"
INCOMPLETE_TASK_STATUSES = ("IN_PROGRESS", "PENDING")

# 区块顺序与类型是销售卡合同。标题只从 card_links 快照读取。
# 快照 title：immediate_today=立即处理，open_risks=建议关注，next_week_confirm=下周待确认。
# 旧快照里的「商机风险」仍按「建议关注」展示。
SECTION_ORDER: tuple[tuple[str, str], ...] = (
    ("immediate_today", ITEM_IMMEDIATE),
    ("open_risks", ITEM_RISK),
    ("next_week_confirm", ITEM_NEXT_WEEK),
)
SECTION_KEYS = {key: item_type for key, item_type in SECTION_ORDER}
_SECTION_TITLE_RENAMES = {"open_risks": {"商机风险": "建议关注"}}


def parse_report_date(value: Any) -> date:
    if value is None or str(value).strip() == "":
        return beijing_today_date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("report_date must be YYYY-MM-DD") from exc


def is_friday(report_date: date) -> bool:
    return report_date.weekday() == FRIDAY


def shows_week_visits(report_date: date) -> bool:
    """周一不放本周已拜访，其余日子都放。"""
    return report_date.weekday() != 0


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


def _parse_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _payload(row: Any) -> dict[str, Any]:
    parsed = _parse_json(_row_value(row, "payload"))
    return parsed if isinstance(parsed, dict) else {}


def _card_link_specs(row: Any) -> list[dict[str, Any]]:
    parsed = _parse_json(_row_value(row, "card_links"))
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("key")]


def _deleted(row: Any) -> bool:
    flag = _row_value(row, "is_deleted")
    return flag in (1, True, "1", "true", "True")


def _include_row(row: Any, *, friday: bool) -> bool:
    if _deleted(row):
        return False
    if _text(_row_value(row, "status")).upper() == STATUS_SUPERSEDED:
        return False
    if _text(_row_value(row, "subject_type")).upper() != SUBJECT_USER:
        return False
    if _text(_row_value(row, "audience")).lower() != AUDIENCE_SALES:
        return False
    item_type = _text(_row_value(row, "item_type"))
    if item_type == ITEM_NEXT_WEEK and not friday:
        return False
    if item_type == ITEM_RISK and not _keep_risk(_payload(row)):
        return False
    return item_type in {ITEM_IMMEDIATE, ITEM_RISK, ITEM_NEXT_WEEK}


def _keep_risk(payload: Mapping[str, Any]) -> bool:
    if payload.get("gap_only") is True or payload.get("not_formal_risk") is True:
        return False
    change = _text(payload.get("risk_change")).lower()
    return change != "unchanged"


def _snapshot_titles(rows: Sequence[Any]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for row in rows:
        for spec in _card_link_specs(row):
            key = _text(spec.get("key"))
            title = _text(spec.get("title"))
            if key in SECTION_KEYS and title and key not in titles:
                titles[key] = _SECTION_TITLE_RENAMES.get(key, {}).get(title, title)
    return titles


def _serialize_item(row: Any, names: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    payload = _payload(row)
    account_id = _text(_row_value(row, "account_id") or payload.get("account_id")) or None
    opportunity_id = _text(_row_value(row, "opportunity_id") or payload.get("opportunity_id")) or None
    account_name = (names.get("accounts") or {}).get(account_id or "") or None
    opportunity_name = (names.get("opportunities") or {}).get(opportunity_id or "") or None
    evidence = payload.get("evidence")
    data = {
        "unique_id": _text(_row_value(row, "unique_id")) or None,
        "item_type": _text(_row_value(row, "item_type")),
        "extract_key": _text(_row_value(row, "extract_key")) or None,
        "claim": _text(payload.get("claim")),
        "why": _text(payload.get("why")),
        "confidence": payload.get("confidence"),
        "evidence": evidence if isinstance(evidence, list) else [],
        "account_id": account_id,
        "account_name": account_name,
        "opportunity_id": opportunity_id,
        "opportunity_name": opportunity_name,
        "why_today": _text(payload.get("why_today")) or None,
        "delay_impact": _text(payload.get("delay_impact")) or None,
        "done_when": _text(payload.get("done_when")) or None,
        "source": _text(payload.get("source")) or None,
        "risk_status": _text(payload.get("risk_status")).lower() or None,
        "risk_change": _text(payload.get("risk_change")).lower() or None,
        "confirm_prompt": _text(payload.get("confirm_prompt")) or None,
        "coverage": _text(payload.get("coverage")).lower() or None,
        "next_week_when": _text(payload.get("next_week_when")) or None,
    }
    from app.services.leader_intervention_actions import interaction_fields

    data.update(interaction_fields(row))
    titles = names.get("todos") or {}
    related = []
    for todo_id in _todo_ids_on_row(row):
        related.append(
            {
                "unique_id": todo_id,
                "title": _text(titles.get(todo_id)) or None,
            }
        )
    data["related_todos"] = related
    return data


def _sort_key(item: Mapping[str, Any]) -> tuple:
    resolved = 1 if item.get("risk_change") == "resolved" else 0
    try:
        confidence = float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (resolved, -confidence)


def _stamp_key(row: Any) -> tuple[str, str]:
    stamp = _row_value(row, "updated_at") or _row_value(row, "created_at")
    if hasattr(stamp, "isoformat"):
        stamp = stamp.isoformat()
    return (_text(stamp), _text(_row_value(row, "unique_id")))


def _copy_immediate(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    names = (
        "unique_id",
        "subject_type",
        "subject_id",
        "audience",
        "status",
        "is_deleted",
        "item_type",
        "extract_key",
        "account_id",
        "opportunity_id",
        "card_links",
        "payload",
        "reject_reason",
        "adopted_todo_id",
        "feedback",
        "feedback_comment",
        "feedback_at",
        "created_at",
        "updated_at",
    )
    return {name: getattr(row, name, None) for name in names}


def _prefer_extract(group: Sequence[Any]) -> Any:
    """已决定的行留下 unique_id 和赞踩、采纳。新行只替换正文。"""
    ordered = sorted(group, key=_stamp_key)
    latest = ordered[-1]
    if _text(_row_value(latest, "status")).upper() in _DECIDED:
        return latest
    decided = [row for row in ordered if _text(_row_value(row, "status")).upper() in _DECIDED]
    if not decided:
        return latest
    data = _copy_immediate(decided[-1])
    data["payload"] = _row_value(latest, "payload")
    return data


def _collapse_extracts(rows: Sequence[Any]) -> list[Any]:
    """同一类型、同一抽取键只留一条，避免重抽后的新行盖住已有决定。"""
    grouped: dict[str, list[Any]] = {}
    for row in rows or []:
        item_type = _text(_row_value(row, "item_type"))
        key = _text(_row_value(row, "extract_key")) or _text(_row_value(row, "unique_id"))
        grouped.setdefault(f"{item_type}|{key}", []).append(row)
    return [_prefer_extract(group) for group in grouped.values()]


def _todo_ids_on_row(row: Any) -> list[str]:
    payload = _payload(row)
    found: list[str] = []
    raw = payload.get("todo_ids")
    if isinstance(raw, list):
        for value in raw:
            todo_id = _text(value)
            if todo_id and todo_id not in found:
                found.append(todo_id)
    one = _text(_row_value(row, "adopted_todo_id") or payload.get("todo_id"))
    if one and one not in found:
        found.append(one)
    return found


def _merge_shared_immediate(rows: Sequence[Any]) -> list[Any]:
    """同一句立即处理只留一条，下面带上它挂的全部任务。"""
    others: list[Any] = []
    buckets: dict[str, list[Any]] = {}
    order: list[str] = []
    for row in rows or []:
        if _text(_row_value(row, "item_type")) != ITEM_IMMEDIATE:
            others.append(row)
            continue
        claim = _text(_payload(row).get("claim"))
        if not claim:
            others.append(row)
            continue
        if claim not in buckets:
            order.append(claim)
            buckets[claim] = []
        buckets[claim].append(row)
    merged: list[Any] = []
    for claim in order:
        group = buckets[claim]
        todo_ids: list[str] = []
        for row in group:
            for todo_id in _todo_ids_on_row(row):
                if todo_id not in todo_ids:
                    todo_ids.append(todo_id)
        if len(group) == 1 or len(todo_ids) < 2:
            merged.extend(group)
            continue
        primary = sorted(group, key=_stamp_key)[0]
        data = _copy_immediate(primary)
        payload = dict(_payload(primary))
        payload["todo_ids"] = todo_ids
        data["payload"] = payload
        kept_todo = _text(_row_value(primary, "adopted_todo_id"))
        data["adopted_todo_id"] = kept_todo or None
        merged.append(data)
    return others + merged


def assemble_sales_highlights(
    rows: Sequence[Any],
    report_date: date,
    names: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> dict[str, Any]:
    """按业务日组装销售今日重点。非周五丢掉下周确认；unchanged 风险不进。"""
    friday = is_friday(report_date)
    kept = _merge_shared_immediate(
        _collapse_extracts([row for row in rows or [] if _include_row(row, friday=friday)])
    )
    titles = _snapshot_titles(kept)
    name_map = names or {"accounts": {}, "opportunities": {}}
    day = report_date.isoformat()
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in kept:
        by_type[_text(_row_value(row, "item_type"))].append(_serialize_item(row, name_map))

    sections: list[dict[str, Any]] = []
    for key, item_type in SECTION_ORDER:
        items = by_type.get(item_type) or []
        if not items:
            continue
        items.sort(key=_sort_key)
        title = titles.get(key) or key
        sections.append(
            {
                "key": key,
                "title": title,
                "item_type": item_type,
                "items": items,
                "url": build_sales_today_highlights_url(day, key),
            }
        )
    return {
        "report_date": day,
        "is_friday": friday,
        "sections": sections,
        "has_content": bool(sections),
    }


def highlights_payload(assembled: Mapping[str, Any], *, subject_id: str) -> dict[str, Any]:
    items: dict[str, list[dict[str, Any]]] = {}
    links: list[dict[str, Any]] = []
    for section in assembled.get("sections") or []:
        key = section["key"]
        full_items = list(section.get("items") or [])
        items[key] = full_items
        url = _text(section.get("url"))
        links.append(
            {
                "key": key,
                "title": section.get("title") or key,
                "count": len(full_items),
                "url": url or None,
                "item_types": [section.get("item_type")],
            }
        )
    return {
        "report_date": assembled.get("report_date"),
        "subject_id": subject_id,
        "items": items,
        "card_links": links,
    }


def empty_highlights_payload(report_date: date) -> dict[str, Any]:
    return {
        "report_date": report_date.isoformat(),
        "subject_id": "",
        "items": {},
        "card_links": [],
    }


def _section_by_key(assembled: Mapping[str, Any], key: str) -> Optional[Mapping[str, Any]]:
    for section in assembled.get("sections") or []:
        if section.get("key") == key:
            return section
    return None


def render_card_markdown(assembled: Mapping[str, Any]) -> str:
    """主卡正文只放最紧急的一条立即处理。标题不跳转，下面固定一个出口。"""
    section = _section_by_key(assembled, "immediate_today")
    if not section:
        return ""
    items = list(section.get("items") or [])
    if not items:
        return ""
    title = _text(section.get("title"))
    key = _text(section.get("key"))
    lines: list[str] = []
    if title and title != key:
        lines.append(f"**{title}**")
    lines.append(_card_line(key, items[0], None))
    more = _section_exit_link(section)
    if more:
        lines.append(more)
    return "\n".join(lines)


def _section_exit_link(section: Mapping[str, Any]) -> str:
    """正文下唯一出口。一条是「查看」；还有没展开的写成「更多（N）」。"""
    total = len(list(section.get("items") or []))
    url = _text(section.get("url"))
    if total <= 0 or not url:
        return ""
    extra = total - 1
    label = "查看" if extra <= 0 else f"更多（{extra}）"
    return f"[{label}]({url})"


def week_task_summary_link(owner_name: str, report_date: date) -> Optional[tuple[str, str]]:
    """本周待完成：本人、预计完成日在本周一到本周日、状态未完成。已逾期的也在其中。"""
    name = _text(owner_name)
    if not name or name == "Unknown":
        return None
    week_start, week_end = get_week_range_from_date(report_date)
    url = build_task_list_page_url(
        owner_name=name,
        due_date__gte=week_start.isoformat(),
        due_date__lte=week_end.isoformat(),
        status__in=list(INCOMPLETE_TASK_STATUSES),
    )
    if not url:
        return None
    return (WEEK_TASK_LINK_LABEL, url)


def week_followup_link(owner_name: str, report_date: date) -> Optional[tuple[str, str]]:
    """本周已拜访：周一不放。其余日子打开已录入的拜访列表，筛本人、日期在本周一到本周日。"""
    if not shows_week_visits(report_date):
        return None
    name = _text(owner_name)
    if not name or name == "Unknown":
        return None
    week_start, week_end = get_week_range_from_date(report_date)
    url = build_visit_list_page_url(
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        recorder=name,
    )
    if not url:
        return None
    return (WEEK_FOLLOWUP_LINK_LABEL, url)


def card_blue_links(assembled: Mapping[str, Any]) -> list[tuple[str, str]]:
    """建议关注、下周待确认。更多立即处理写在正文后面。"""
    links: list[tuple[str, str]] = []
    for section in assembled.get("sections") or []:
        title = _text(section.get("title"))
        url = _text(section.get("url"))
        key = _text(section.get("key"))
        total = len(section.get("items") or [])
        if key == "immediate_today" or not url or not title or title == key or total <= 0:
            continue
        label = f"{title}（{total}）" if total > 1 else title
        links.append((label, url))
    return links


def card_action_buttons(
    assembled: Mapping[str, Any],
    *,
    owner_name: str = "",
) -> list[tuple[str, str]]:
    """本周待完成（任务）；周一以外再加本周已拜访（已录入的拜访）。"""
    report_day = _report_day(assembled)
    if report_day is None:
        return []
    buttons: list[tuple[str, str]] = []
    task_link = week_task_summary_link(owner_name, report_day)
    if task_link:
        buttons.append(task_link)
    followup_link = week_followup_link(owner_name, report_day)
    if followup_link:
        buttons.append(followup_link)
    return buttons


def render_card_links(assembled: Mapping[str, Any]) -> str:
    return "  ".join(f"[{label}]({url})" for label, url in card_blue_links(assembled))


def render_action_buttons(buttons: Sequence[tuple[str, str]]) -> str:
    return "  ".join(f"[{label}]({url})" for label, url in buttons)


def render_dingtalk_markdown(
    assembled: Mapping[str, Any],
    *,
    title: str,
    owner_name: str = "",
) -> str:
    """钉钉 sampleMarkdown：一级标题、二级小节、引用说明。底部链接不用列表，避免看成「更多」的展开。"""
    lines: list[str] = [f"# {title}"]
    section = _section_by_key(assembled, "immediate_today")
    items = list((section or {}).get("items") or [])
    if section and items:
        heading = _text(section.get("title"))
        key = _text(section.get("key"))
        if heading and heading != key:
            lines.append(f"## {heading}")
        lines.extend(_dingtalk_item_lines(key, items[0]))
        exit_link = _section_exit_link(section)
        if exit_link:
            lines.append(exit_link)
    body = "\n".join(lines)
    tail_parts = [
        part
        for part in (
            render_card_links(assembled),
            render_action_buttons(card_action_buttons(assembled, owner_name=owner_name)),
        )
        if part
    ]
    if not tail_parts:
        return body
    return f"{body}\n\n\n" + "\n\n".join(tail_parts)


def _dingtalk_item_lines(section_key: str, item: Mapping[str, Any]) -> list[str]:
    context = " · ".join(
        part
        for part in (_text(item.get("account_name")), _text(item.get("opportunity_name")))
        if part
    )
    if section_key == "immediate_today":
        head = _text(item.get("claim"))
        extra = _text(item.get("why_today"))
    elif section_key == "open_risks":
        claim = _text(item.get("claim"))
        head = f"已闭环：{claim}" if item.get("risk_change") == "resolved" else claim
        extra = ""
    else:
        head = _text(item.get("confirm_prompt")) or _text(item.get("claim"))
        extra = ""
    lines: list[str] = []
    if head:
        lines.append(head)
    if extra:
        lines.append(f"> {extra}")
    if context:
        lines.append(f"*{context}*")
    if section_key == "immediate_today":
        for label in _related_todo_labels(item):
            lines.append(f"- {label}")
    return lines


def _related_todo_labels(item: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for todo in item.get("related_todos") or []:
        if not isinstance(todo, Mapping):
            continue
        label = _text(todo.get("title")) or _text(todo.get("unique_id"))
        if label and label not in labels:
            labels.append(label)
    return labels


def _report_day(assembled: Mapping[str, Any]) -> Optional[date]:
    raw = assembled.get("report_date")
    if isinstance(raw, date):
        return raw
    text = _text(raw)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _card_line(section_key: str, item: Mapping[str, Any], index: Optional[int]) -> str:
    context = " · ".join(
        part
        for part in (_text(item.get("account_name")), _text(item.get("opportunity_name")))
        if part
    )
    if section_key == "immediate_today":
        head = _text(item.get("claim"))
        extra = _text(item.get("why_today"))
    elif section_key == "open_risks":
        claim = _text(item.get("claim"))
        head = f"已闭环：{claim}" if item.get("risk_change") == "resolved" else claim
        extra = ""
    else:
        head = _text(item.get("confirm_prompt")) or _text(item.get("claim"))
        extra = ""
    parts = [head if index is None else f"{index}. {head}"]
    if context:
        parts.append(f"   {context}")
    if extra:
        parts.append(f"   {extra}")
    if section_key == "immediate_today":
        for label in _related_todo_labels(item):
            parts.append(f"   - {label}")
    return "\n".join(parts)


def _subject_id(row: Any) -> str:
    return _text(_row_value(row, "subject_id"))


def load_sales_extract_rows(
    session: Session,
    report_date: date,
    oauth_user_ids: Optional[Sequence[str]] = None,
) -> Optional[list[Any]]:
    """读取当天一线销售有效抽取。表不可用时返回 None。"""
    ids = [_text(item) for item in (oauth_user_ids or []) if _text(item)]
    try:
        statement = select(CRMSkillExtractItem).where(
            CRMSkillExtractItem.report_date == report_date,
            CRMSkillExtractItem.subject_type == SUBJECT_USER,
            CRMSkillExtractItem.audience == AUDIENCE_SALES,
            CRMSkillExtractItem.is_deleted == 0,
            CRMSkillExtractItem.status != STATUS_SUPERSEDED,
        )
        if oauth_user_ids is not None:
            if not ids:
                return []
            statement = statement.where(CRMSkillExtractItem.subject_id.in_(ids))
        rows = session.exec(statement).all()
        return list(rows)
    except Exception as exc:
        logger.warning("load sales today highlights failed date=%s: %s", report_date, exc)
        return None


def load_display_names(session: Session, rows: Sequence[Any]) -> dict[str, dict[str, str]]:
    account_ids = {
        _text(_row_value(row, "account_id") or _payload(row).get("account_id"))
        for row in rows
    }
    opportunity_ids = {
        _text(_row_value(row, "opportunity_id") or _payload(row).get("opportunity_id"))
        for row in rows
    }
    account_ids.discard("")
    opportunity_ids.discard("")
    todo_ids = []
    for row in rows:
        for todo_id in _todo_ids_on_row(row):
            if todo_id not in todo_ids:
                todo_ids.append(todo_id)
    accounts: dict[str, str] = {}
    opportunities: dict[str, str] = {}
    todos: dict[str, str] = {}
    try:
        if account_ids:
            found = session.exec(
                select(CRMAccount).where(CRMAccount.unique_id.in_(list(account_ids)))
            ).all()
            for row in found:
                uid = _text(getattr(row, "unique_id", None))
                name = _text(getattr(row, "customer_name", None))
                if uid and name:
                    accounts[uid] = name
        if opportunity_ids:
            found = session.exec(
                select(CRMOpportunity).where(CRMOpportunity.unique_id.in_(list(opportunity_ids)))
            ).all()
            for row in found:
                uid = _text(getattr(row, "unique_id", None))
                name = _text(getattr(row, "opportunity_name", None))
                if uid and name:
                    opportunities[uid] = name
        if todo_ids:
            from app.services.subject_interaction import load_task_summaries

            for todo_id, summary in load_task_summaries(session, todo_ids).items():
                title = _text(summary.get("title"))
                if title:
                    todos[todo_id] = title
    except Exception as exc:
        logger.warning("load highlight display names failed: %s", exc)
    return {"accounts": accounts, "opportunities": opportunities, "todos": todos}


def highlights_for_viewer(
    session: Session,
    viewer_oauth_user_id: Optional[str],
    report_date: date,
) -> dict[str, Any]:
    """只查当前登录人自己的 oauth_user_id。"""
    oauth_id = _text(viewer_oauth_user_id)
    if not oauth_id:
        return empty_highlights_payload(report_date)
    rows = load_sales_extract_rows(session, report_date, oauth_user_ids=[oauth_id])
    if not rows:
        payload = empty_highlights_payload(report_date)
        payload["subject_id"] = oauth_id
        return payload
    names = load_display_names(session, rows)
    assembled = assemble_sales_highlights(rows, report_date, names)
    return highlights_payload(assembled, subject_id=oauth_id)


def _group_by_subject(rows: Sequence[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        subject = _subject_id(row)
        if subject:
            grouped[subject].append(row)
    return grouped


def _recipient_from_profile(profile: Any) -> Optional[dict[str, Any]]:
    if profile is None or not getattr(profile, "is_active", True):
        return None
    user_id = getattr(profile, "user_id", None)
    if not user_id:
        return None
    account = getattr(profile, "oauth_user", None)
    open_id = _text(getattr(account, "open_id", None))
    platform = _text(getattr(account, "provider", None))
    if not open_id or not platform:
        return None
    return {
        "open_id": open_id,
        "name": _text(getattr(profile, "name", None)) or "Unknown",
        "type": "sales",
        "department": getattr(profile, "department", None),
        "receive_id_type": "open_id",
        "platform": platform,
        "user_id": str(user_id),
        "userId": str(user_id),
    }


def deliver_highlight_card(
    db_session: Session,
    recipient: Mapping[str, Any],
    assembled: Mapping[str, Any],
) -> dict[str, Any]:
    """飞书 / Lark：正文一条立即处理，风险为蓝链。本周待完成每天都有，周一以外再加本周已拜访。"""
    from app.services.platform_notification_service import platform_notification_service

    title = f"今日重点｜{assembled.get('report_date')}"
    body = render_card_markdown(assembled)
    links = render_card_links(assembled)
    buttons = card_action_buttons(assembled, owner_name=_text(recipient.get("name")))
    platform = _text(recipient.get("platform"))
    if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
        card = platform_notification_service.build_feishu_markdown_card(
            body or links,
            title=title,
            footer=links if body and links else None,
            footer_text_size="normal",
            footer_buttons=buttons or None,
        )
        tokens = platform_notification_service._get_platform_tokens([platform])
        if platform not in tokens:
            return {"success": False, "message": "Failed to get token"}
        success_count, failed = platform_notification_service._send_messages_to_platform(
            platform,
            [dict(recipient)],
            tokens,
            card,
        )
        return {
            "success": success_count > 0,
            "message": None if success_count else "send failed",
            "failed_recipients": failed,
        }

    text = render_dingtalk_markdown(
        assembled,
        title=title,
        owner_name=_text(recipient.get("name")),
    )
    if platform == PLATFORM_DINGTALK or platform:
        return platform_notification_service.send_platform_notification(
            db_session,
            recipient_user_id=_text(recipient.get("user_id")),
            content=text or title,
            content_type="markdown",
            title=title,
            delivery="card",
        )
    return {"success": False, "message": "unsupported platform"}


def _base_result(report_date: date) -> dict[str, Any]:
    return {
        "success": False,
        "skipped": False,
        "skip_reason": None,
        "scene": SCENE_SALES_DAILY,
        "variant": VARIANT_TODAY_HIGHLIGHTS,
        "report_date": report_date.isoformat(),
        "recipients_count": 0,
        "success_count": 0,
        "failed_recipients": [],
        "items": [],
    }


def handle_sales_today_highlights(
    db_session: Session,
    *,
    report_date: Optional[date | str] = None,
    recipient_user_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """推送一线销售今日重点。变体关闭、空日、无 open_id、本人关掉都不发空卡。"""
    from app.repositories.user_profile import user_profile_repo

    target = parse_report_date(report_date)
    result = _base_result(target)
    policy = load_report_push_policy()
    if not policy.variant_enabled(SCENE_SALES_DAILY, VARIANT_TODAY_HIGHLIGHTS):
        logger.info("sales today highlights disabled")
        return {**result, "skipped": True, "skip_reason": "variant_disabled"}

    rows = load_sales_extract_rows(
        db_session,
        target,
        oauth_user_ids=recipient_user_ids,
    )
    if rows is None:
        return {**result, "skipped": True, "skip_reason": "source_unavailable"}
    grouped = _group_by_subject(rows)
    if recipient_user_ids is not None:
        wanted = {_text(item) for item in recipient_user_ids if _text(item)}
        grouped = {key: value for key, value in grouped.items() if key in wanted}
    if not grouped:
        return {**result, "skipped": True, "skip_reason": "empty_highlights"}

    names = load_display_names(db_session, rows)
    items: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    success_count = 0
    recipients_count = 0

    for subject_id, subject_rows in grouped.items():
        assembled = assemble_sales_highlights(subject_rows, target, names)
        item = {"subject_id": subject_id, "sent": False, "skipped": False, "skip_reason": None}
        if not assembled["has_content"]:
            item["skipped"] = True
            item["skip_reason"] = "empty_highlights"
            items.append(item)
            continue
        profile = user_profile_repo.get_by_oauth_user_id(db_session, subject_id)
        recipient = _recipient_from_profile(profile)
        if recipient is None:
            item["skipped"] = True
            item["skip_reason"] = "no_open_id"
            items.append(item)
            continue
        kept = filter_opted_out_recipients(
            db_session,
            [recipient],
            scene=SCENE_SALES_DAILY,
            variant=VARIANT_TODAY_HIGHLIGHTS,
            department_ids=[""],
        )
        if not kept:
            item["skipped"] = True
            item["skip_reason"] = "opted_out"
            items.append(item)
            continue
        recipients_count += 1
        sent = deliver_highlight_card(db_session, kept[0], assembled)
        if sent.get("success"):
            success_count += 1
            item["sent"] = True
        else:
            item["skipped"] = True
            item["skip_reason"] = "send_failed"
            failed.extend(sent.get("failed_recipients") or [])
            if sent.get("message"):
                failed.append({"recipient_user_id": recipient["user_id"], "message": sent["message"]})
        items.append(item)

    skip_reason = _aggregate_skip_reason(items, success_count)
    return {
        **result,
        "success": success_count > 0,
        "skipped": success_count == 0,
        "skip_reason": skip_reason,
        "recipients_count": recipients_count,
        "success_count": success_count,
        "failed_recipients": failed,
        "items": items,
    }


def _aggregate_skip_reason(items: Iterable[Mapping[str, Any]], success_count: int) -> Optional[str]:
    if success_count:
        return None
    reasons = [_text(item.get("skip_reason")) for item in items if item.get("skip_reason")]
    unique = list(dict.fromkeys(reason for reason in reasons if reason))
    if len(unique) == 1:
        return unique[0]
    if unique:
        return "no_deliverable_recipients"
    return "empty_highlights"

"""销售负责人今日重点：只读 USER + audience=leader 的 TODAY_INTERVENTION。

subject_id 是负责人 oauth id。不消费一线 audience=sales 的抽取，不进部门群，不抄送。
入口标题用抽取行上的 card_links 快照。
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
from app.services.leader_insight_reports import (
    load_leader_insight_reports,
    load_leader_insight_sections,
    report_entry_lines,
)
from app.services.leader_visit_watch import load_leader_visit_watch, visit_watch_lines
from app.services.notification_delivery_preference import filter_opted_out_recipients
from app.services.notification_scene_catalog import (
    SCENE_DEPARTMENT_HIGHLIGHTS,
    VARIANT_TODAY_HIGHLIGHTS,
)
from app.services.report_push_policy import load_report_push_policy
from app.services.today_highlights_billing import (
    check_today_highlights_quota,
    report_today_highlights_usage,
)
from app.services.sales_today_highlights import parse_report_date
from app.utils.date_utils import get_week_range_from_date
from app.utils.push_page_urls import build_department_highlights_url, build_week_visit_list_url

logger = logging.getLogger(__name__)

SUBJECT_USER = "USER"
AUDIENCE_LEADER = "leader"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_ADOPTED = "ADOPTED"
STATUS_REJECTED = "REJECTED"
_DECIDED = {STATUS_ADOPTED, STATUS_REJECTED}
ITEM_INTERVENTION = "TODAY_INTERVENTION"
SECTION_KEY = "leader_intervene"
REPORTS_ONLY_LEAD = "今天没有需介入事项，可以先看这些报告。"


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


def _include_row(row: Any) -> bool:
    if _deleted(row):
        return False
    if _text(_row_value(row, "status")).upper() == STATUS_SUPERSEDED:
        return False
    if _text(_row_value(row, "subject_type")).upper() != SUBJECT_USER:
        return False
    if _text(_row_value(row, "audience")).lower() != AUDIENCE_LEADER:
        return False
    return _text(_row_value(row, "item_type")) == ITEM_INTERVENTION


def _snapshot_title(rows: Sequence[Any]) -> str:
    for row in rows:
        for spec in _card_link_specs(row):
            if _text(spec.get("key")) == SECTION_KEY:
                title = _text(spec.get("title"))
                if title:
                    return title
    return ""


def _interaction_defaults(row: Any) -> dict[str, Any]:
    from app.services.leader_intervention_actions import interaction_fields

    fields = interaction_fields(row)
    fields["comments"] = []
    fields["tasks"] = []
    return fields


def _confidence(item: Mapping[str, Any]) -> float:
    try:
        return float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def _serialize_item(row: Any, names: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    payload = _payload(row)
    account_id = _text(_row_value(row, "account_id") or payload.get("account_id")) or None
    opportunity_id = _text(_row_value(row, "opportunity_id") or payload.get("opportunity_id")) or None
    related_user_id = _text(payload.get("related_user_id")) or None
    evidence = payload.get("evidence")
    people = names.get("people") or {}
    return {
        "unique_id": _text(_row_value(row, "unique_id")) or None,
        "item_type": ITEM_INTERVENTION,
        "extract_key": _text(_row_value(row, "extract_key")) or None,
        "claim": _text(payload.get("claim")),
        "why": _text(payload.get("why")),
        "confidence": payload.get("confidence"),
        "evidence": evidence if isinstance(evidence, list) else [],
        "account_id": account_id,
        "account_name": (names.get("accounts") or {}).get(account_id or "") or None,
        "opportunity_id": opportunity_id,
        "opportunity_name": (names.get("opportunities") or {}).get(opportunity_id or "") or None,
        "related_user_id": related_user_id,
        "related_user_name": people.get(related_user_id or "") or None,
        "management_issue": _text(payload.get("management_issue")) or None,
        "today_window": _text(payload.get("today_window")) or None,
        "leader_action": _text(payload.get("leader_action")) or None,
        "verify_after": _text(payload.get("verify_after")) or None,
        "intervention_type": _text(payload.get("intervention_type")).lower() or None,
        **_interaction_defaults(row),
    }


def _row_status(row: Any) -> str:
    return _text(_row_value(row, "status")).upper()


def _row_sort_key(row: Any) -> tuple[str, str]:
    stamp = _row_value(row, "updated_at") or _row_value(row, "created_at")
    if hasattr(stamp, "isoformat"):
        stamp = stamp.isoformat()
    return (_text(stamp), _text(_row_value(row, "unique_id")))


def _row_dict(row: Any) -> dict[str, Any]:
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
        "report_date",
        "reject_reason",
        "adopted_todo_id",
        "feedback",
        "feedback_comment",
        "feedback_at",
        "feedback_user_id",
        "created_at",
        "updated_at",
    )
    return {name: getattr(row, name, None) for name in names}


def _with_latest_body(decided: Any, latest: Any) -> dict[str, Any]:
    """身份和决定留在不会被作废的旧行上，正文用新行。"""
    data = _row_dict(decided)
    data["payload"] = _row_value(latest, "payload")
    return data


def _prefer_one(group: Sequence[Any]) -> Any:
    ordered = sorted(group, key=_row_sort_key)
    latest = ordered[-1]
    if _row_status(latest) in _DECIDED:
        return latest
    decided = [row for row in ordered if _row_status(row) in _DECIDED]
    if not decided:
        return latest
    return _with_latest_body(decided[-1], latest)


def _collapse_rows(rows: Sequence[Any]) -> list[Any]:
    """同一天、同一负责人、同一抽取键只留一条。重抽产生的新行接上已有决定。"""
    from app.services.subject_interaction import intervention_anchor

    grouped: dict[str, list[Any]] = {}
    for row in rows or []:
        if not _include_row(row):
            continue
        anchor = intervention_anchor(
            _row_value(row, "report_date"),
            _subject_id(row),
            _row_value(row, "extract_key"),
            _row_value(row, "unique_id"),
        )
        grouped.setdefault(anchor, []).append(row)
    return [_prefer_one(group) for group in grouped.values()]


def assemble_department_highlights(
    rows: Sequence[Any],
    report_date: date,
    department_id: str,
    names: Optional[Mapping[str, Mapping[str, str]]] = None,
    *,
    department_name: str = "",
) -> dict[str, Any]:
    """组装一位负责人当天的今日需介入。主体是负责人 oauth id。"""
    kept = _collapse_rows(rows)
    name_map = names or {"accounts": {}, "opportunities": {}, "people": {}}
    items = [_serialize_item(row, name_map) for row in kept]
    items.sort(key=_confidence, reverse=True)
    title = _snapshot_title(kept)
    day = report_date.isoformat()
    dept = _text(department_id)
    url = build_department_highlights_url(day, dept, SECTION_KEY) if items else ""
    section = None
    if items:
        section = {
            "key": SECTION_KEY,
            "title": title or SECTION_KEY,
            "item_type": ITEM_INTERVENTION,
            "items": items,
            "url": url,
        }
    return {
        "report_date": day,
        "department_id": dept,
        "department_name": _text(department_name),
        "sections": [section] if section else [],
        "has_content": bool(items),
    }


def assemble_leader_highlights(
    department_cards: Sequence[Mapping[str, Any]],
    report_date: date,
) -> dict[str, Any]:
    """把一位负责人名下多个部门收成一张卡。多部门时主条带部门名。"""
    entries: list[dict[str, Any]] = []
    for card in department_cards or []:
        sections = list(card.get("sections") or [])
        if not sections:
            continue
        section = sections[0]
        dept_name = _text(card.get("department_name"))
        dept_id = _text(card.get("department_id"))
        escalated = bool(card.get("escalated"))
        for item in section.get("items") or []:
            copied = dict(item)
            copied["_department_name"] = dept_name
            copied["_department_id"] = dept_id
            copied["_section_title"] = section.get("title")
            copied["_force_department"] = escalated
            entries.append(copied)
    entries.sort(key=_confidence, reverse=True)
    department_ids = list(dict.fromkeys(entry["_department_id"] for entry in entries if entry["_department_id"]))
    show_department = len(department_ids) > 1
    items: list[dict[str, Any]] = []
    for entry in entries:
        item = {key: value for key, value in entry.items() if not str(key).startswith("_")}
        if (show_department or entry["_force_department"]) and entry["_department_name"]:
            item["department_name"] = entry["_department_name"]
        items.append(item)
    title = _text(entries[0].get("_section_title")) if entries else ""
    department_names = list(
        dict.fromkeys(
            _text(card.get("department_name"))
            for card in department_cards or []
            if _text(card.get("department_name"))
        )
    )
    day = report_date.isoformat()
    if len(items) == 1 and len(department_ids) == 1:
        link_department = department_ids[0]
    else:
        link_department = ""
    url = build_department_highlights_url(day, link_department, SECTION_KEY) if items else ""
    section = None
    if items:
        section = {
            "key": SECTION_KEY,
            "title": title or SECTION_KEY,
            "item_type": ITEM_INTERVENTION,
            "items": items,
            "url": url,
        }
    return {
        "report_date": day,
        "department_id": department_ids[0] if len(department_ids) == 1 else "",
        "department_name": "",
        "department_names": department_names,
        "department_ids": department_ids,
        "sections": [section] if section else [],
        "has_content": bool(items),
    }


def department_highlights_payload(assembled: Mapping[str, Any]) -> dict[str, Any]:
    items: dict[str, list[dict[str, Any]]] = {}
    links: list[dict[str, Any]] = []
    for section in assembled.get("sections") or []:
        full_items = list(section.get("items") or [])
        key = section["key"]
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
        "items": items,
        "card_links": links,
    }


def _intervention_markdown(assembled: Mapping[str, Any]) -> str:
    """介入块：标题、展开的一条、未展开的人名、出口链接。"""
    sections = list(assembled.get("sections") or [])
    if not sections:
        return ""
    section = sections[0]
    items = list(section.get("items") or [])
    if not items:
        return ""
    lines: list[str] = []
    title = _text(section.get("title"))
    key = _text(section.get("key"))
    if title and title != key:
        lines.append(f"**{title}**")
    lines.append(_card_line(items[0]))
    others = _other_people_line(items)
    if others:
        lines.append(others)
    more = _section_exit_link(section)
    if more:
        lines.append(more)
    return "\n".join(lines)


def render_card_sections(assembled: Mapping[str, Any]) -> list[str]:
    """飞书按块发送。介入一块；周一、周五的拜访对照单独一块；报告入口再一块。"""
    intervention = _intervention_markdown(assembled)
    watch = "\n".join(visit_watch_lines(assembled.get("visit_watch")))
    reports = "\n".join(
        _report_card_lines(assembled, has_other_blocks=bool(intervention or watch))
    )
    return [part for part in (intervention, watch, reports) if part]


def _report_card_lines(assembled: Mapping[str, Any], *, has_other_blocks: bool) -> list[str]:
    """报告入口一行蓝链。只有报告时先补一句说明，避免卡片太空。"""
    links = report_entry_lines(assembled.get("report_entries"))
    if not links:
        return []
    if has_other_blocks:
        return links
    return [REPORTS_ONLY_LEAD, *links]


def render_card_markdown(assembled: Mapping[str, Any]) -> str:
    """正文只展开信度最高的一条介入，其余人只点名。标题不跳转，下面固定一个出口。"""
    return "\n\n".join(render_card_sections(assembled))


def _section_exit_link(section: Mapping[str, Any]) -> str:
    """正文下唯一出口。一条是「查看」；还有没展开的写成「更多（N）」。"""
    total = len(list(section.get("items") or []))
    url = _text(section.get("url"))
    if total <= 0 or not url:
        return ""
    extra = total - 1
    label = "查看" if extra <= 0 else f"更多（{extra}）"
    return f"[{label}]({url})"


def _other_people_line(items: Sequence[Mapping[str, Any]]) -> str:
    """没展开的介入只写姓名，避免被看成「更多」的子项。"""
    shown = _text(items[0].get("related_user_name")) or _text(items[0].get("related_user_id"))
    names: list[str] = []
    for item in list(items)[1:]:
        name = _text(item.get("related_user_name")) or _text(item.get("related_user_id"))
        if name and name != shown and name not in names:
            names.append(name)
    if not names:
        return ""
    return "还有" + "、".join(names)


WEEK_VISIT_LABEL = "本周已拜访"


def _report_day(assembled: Mapping[str, Any]) -> Optional[date]:
    raw = _text(assembled.get("report_date"))
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _visit_department_names(assembled: Mapping[str, Any]) -> list[str]:
    names = [_text(name) for name in (assembled.get("department_names") or []) if _text(name)]
    single = _text(assembled.get("department_name"))
    if single and single not in names:
        names.append(single)
    return list(dict.fromkeys(names))


def card_footer_buttons(assembled: Mapping[str, Any]) -> list[tuple[str, str]]:
    """本周已拜访：周一不放。周一、周五有拜访对照时，入口改在那段末尾，这里不再重复。"""
    watch = assembled.get("visit_watch") or {}
    if _text(watch.get("url")):
        return []
    report_day = _report_day(assembled)
    if report_day is None or report_day.weekday() == 0:
        return []
    week_start, week_end = get_week_range_from_date(report_day)
    url = build_week_visit_list_url(
        week_start.isoformat(),
        week_end.isoformat(),
        _visit_department_names(assembled),
    )
    if not url:
        return []
    return [(WEEK_VISIT_LABEL, url)]


def render_dingtalk_markdown(assembled: Mapping[str, Any], *, title: str) -> str:
    """钉钉 sampleMarkdown：一级标题、二级小节、引用说明。底部链接不用列表，避免看成「更多」的展开。"""
    lines: list[str] = [f"# {title}"]
    sections = list(assembled.get("sections") or [])
    section = sections[0] if sections else None
    items = list((section or {}).get("items") or [])
    if section and items:
        heading = _text(section.get("title"))
        key = _text(section.get("key"))
        if heading and heading != key:
            lines.append(f"## {heading}")
        lines.extend(_dingtalk_item_lines(items[0]))
        others = _other_people_line(items)
        if others:
            lines.append(others)
        exit_link = _section_exit_link(section)
        if exit_link:
            lines.append(exit_link)
    watch_lines = visit_watch_lines(assembled.get("visit_watch"), dingtalk=True)
    if lines and watch_lines:
        lines.append("")
    lines.extend(watch_lines)
    report_lines = _report_card_lines(
        assembled,
        has_other_blocks=bool(items) or bool(watch_lines),
    )
    if lines and report_lines:
        lines.append("")
    lines.extend(report_lines)
    body = "\n".join(lines)
    tail = "  ".join(f"[{label}]({url})" for label, url in card_footer_buttons(assembled))
    if not tail:
        return body
    return f"{body}\n\n\n{tail}"


def _dingtalk_item_lines(item: Mapping[str, Any]) -> list[str]:
    who = _text(item.get("related_user_name")) or _text(item.get("related_user_id"))
    claim = _text(item.get("claim"))
    dept = _text(item.get("department_name"))
    lines: list[str] = []
    if who and dept:
        lines.append(f"**{who}** · {dept}")
    elif who:
        lines.append(f"**{who}**")
    if claim and claim != who:
        lines.append(claim)
    action = _text(item.get("leader_action"))
    if action:
        lines.append(f"> {action}")
    context = " · ".join(
        part
        for part in (_text(item.get("account_name")), _text(item.get("opportunity_name")))
        if part
    )
    if context:
        lines.append(f"*{context}*")
    return lines


def _card_line(item: Mapping[str, Any]) -> str:
    """人名单独加粗。情况说明和建议动作之间空一行，避免粘成一段。"""
    who = _text(item.get("related_user_name")) or _text(item.get("related_user_id"))
    claim = _text(item.get("claim"))
    dept = _text(item.get("department_name"))
    lines: list[str] = []
    if who:
        lines.append(f"**{who}** · {dept}" if dept else f"**{who}**")
        if claim and claim != who:
            lines.append(claim)
    elif claim:
        lines.append(f"{claim} · {dept}" if dept else claim)
    action = _text(item.get("leader_action"))
    if action:
        if lines:
            lines.append("")
        lines.append(action)
    context = " · ".join(
        part
        for part in (_text(item.get("account_name")), _text(item.get("opportunity_name")))
        if part
    )
    if context:
        lines.append(context)
    return "\n".join(lines)


def _subject_id(row: Any) -> str:
    return _text(_row_value(row, "subject_id"))


def load_department_extract_rows(
    session: Session,
    report_date: date,
    oauth_user_ids: Optional[Sequence[str]] = None,
) -> Optional[list[Any]]:
    """读取当天负责人的今日需介入。subject_id 是负责人 oauth id。表不可用时返回 None。"""
    ids = [_text(item) for item in (oauth_user_ids or []) if _text(item)]
    try:
        statement = select(CRMSkillExtractItem).where(
            CRMSkillExtractItem.report_date == report_date,
            CRMSkillExtractItem.subject_type == SUBJECT_USER,
            CRMSkillExtractItem.audience == AUDIENCE_LEADER,
            CRMSkillExtractItem.is_deleted == 0,
            CRMSkillExtractItem.status != STATUS_SUPERSEDED,
        )
        if oauth_user_ids is not None:
            if not ids:
                return []
            statement = statement.where(CRMSkillExtractItem.subject_id.in_(ids))
        return list(session.exec(statement).all())
    except Exception as exc:
        logger.warning(
            "load department today highlights failed date=%s: %s",
            report_date,
            exc,
        )
        return None


def load_display_names(session: Session, rows: Sequence[Any]) -> dict[str, dict[str, str]]:
    from app.repositories.user_profile import user_profile_repo

    account_ids: set[str] = set()
    opportunity_ids: set[str] = set()
    people_ids: set[str] = set()
    for row in rows:
        payload = _payload(row)
        account_ids.add(_text(_row_value(row, "account_id") or payload.get("account_id")))
        opportunity_ids.add(_text(_row_value(row, "opportunity_id") or payload.get("opportunity_id")))
        people_ids.add(_text(payload.get("related_user_id")))
    account_ids.discard("")
    opportunity_ids.discard("")
    people_ids.discard("")
    accounts: dict[str, str] = {}
    opportunities: dict[str, str] = {}
    people: dict[str, str] = {}
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
        if people_ids:
            profiles = user_profile_repo.get_by_oauth_user_ids(session, list(people_ids))
            for profile in profiles:
                oauth_id = _text(getattr(profile, "oauth_user_id", None))
                name = _text(getattr(profile, "name", None))
                if oauth_id and name:
                    people[oauth_id] = name
    except Exception as exc:
        logger.warning("load department highlight names failed: %s", exc)
    return {"accounts": accounts, "opportunities": opportunities, "people": people}


def _group_by_subject(rows: Sequence[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        subject = _subject_id(row)
        if subject:
            grouped[subject].append(row)
    return grouped


def department_ids_led_by(session: Session, user_id: Any) -> set[str]:
    """当前用户负责的部门 unique_id。OAuth 名单优先，失败再看本人档案。"""
    from app.repositories.department_mirror import department_mirror_repo
    from app.repositories.user_profile import user_profile_repo
    from app.services.oauth_service import oauth_client

    uid = _text(user_id)
    names: set[str] = set()
    oauth_map: Mapping[str, Any] = {}
    oauth_failed = False
    try:
        oauth_map = oauth_client.get_departments_with_leaders() or {}
    except Exception as exc:
        oauth_failed = True
        logger.warning("load led departments failed user=%s: %s", uid, exc)
    for name, leaders in oauth_map.items():
        for leader in leaders or []:
            if not isinstance(leader, Mapping):
                continue
            leader_id = _text(leader.get("userId") or leader.get("user_id"))
            if uid and leader_id == uid:
                dept_name = _text(name)
                if dept_name:
                    names.add(dept_name)
    if not names and (oauth_failed or not oauth_map):
        profile = None
        try:
            from uuid import UUID

            profile = user_profile_repo.get_by_user_id(session, UUID(uid))
        except (TypeError, ValueError):
            profile = None
        if (
            profile is not None
            and getattr(profile, "is_active", True)
            and getattr(profile, "direct_manager_id", None) in (None, "")
        ):
            own_dept = _text(getattr(profile, "department", None))
            if own_dept:
                names.add(own_dept)
    ids: set[str] = set()
    for name in names:
        ids.update(department_mirror_repo.get_department_ids_by_name(session, name))
    return ids


def escalated_child_department_ids(session: Session, led: set[str]) -> set[str]:
    """没有负责人、推送时会上探一级的直接下级部门。"""
    if not led:
        return set()
    try:
        from app.models.department_mirror import DepartmentMirror
        from app.services.platform_notification_service import platform_notification_service

        rows = session.exec(
            select(
                DepartmentMirror.unique_id,
                DepartmentMirror.department_name,
            ).where(
                DepartmentMirror.is_active == True,  # noqa: E712
                DepartmentMirror.parent_id.in_(list(led)),
            )
        ).all()
    except Exception as exc:
        logger.warning("读取上探部门失败: %s", exc)
        return set()
    if not isinstance(rows, list):
        return set()
    extra: set[str] = set()
    for row in rows:
        if isinstance(row, Mapping):
            child_id = _text(row.get("unique_id"))
            name = _text(row.get("department_name"))
        elif hasattr(row, "_mapping"):
            mapped = row._mapping
            child_id = _text(mapped.get("unique_id"))
            name = _text(mapped.get("department_name"))
        elif isinstance(row, (tuple, list)):
            child_id = _text(row[0] if len(row) > 0 else None)
            name = _text(row[1] if len(row) > 1 else None)
        else:
            continue
        if not child_id or child_id in led or not name:
            continue
        try:
            recipients = platform_notification_service.resolve_department_report_recipients(session, name) or []
        except Exception as exc:
            logger.warning("判断下级部门负责人失败 dept=%s: %s", child_id, exc)
            continue
        if recipients:
            continue
        extra.add(child_id)
    return extra


def department_ids_in_scope(session: Session, user_id: Any) -> set[str]:
    """能看、能写的部门：自己负责的，加上上探到自己的下级部门。"""
    led = department_ids_led_by(session, user_id)
    return led | escalated_child_department_ids(session, led)


def leader_oauth_id(session: Session, user_id: Any) -> str:
    """登录用户对应的 oauth id。抽取行的 subject_id 用这个值。"""
    from app.repositories.user_profile import user_profile_repo

    uid = _text(user_id)
    if not uid:
        return ""
    profile = None
    try:
        from uuid import UUID

        profile = user_profile_repo.get_by_user_id(session, UUID(uid))
    except (TypeError, ValueError):
        profile = None
    if profile is None:
        try:
            profile = user_profile_repo.get_by_user_id(session, uid)
        except Exception:
            profile = None
    return _text(getattr(profile, "oauth_user_id", None)) if profile else ""


def empty_leader_highlights_payload(report_date: date, *, subject_id: str = "") -> dict[str, Any]:
    return {
        "report_date": report_date.isoformat(),
        "subject_id": subject_id,
        "items": {},
        "card_links": [],
    }


def highlights_for_leader(
    session: Session,
    user_id: Any,
    report_date: date,
) -> dict[str, Any]:
    """返回当前负责人自己的今日需介入和报告。subject_id 是其 oauth id。"""
    oauth_id = leader_oauth_id(session, user_id)
    if not oauth_id:
        return empty_leader_highlights_payload(report_date)
    rows = load_department_extract_rows(session, report_date, oauth_user_ids=[oauth_id]) or []
    names = load_display_names(session, rows) if rows else {"accounts": {}, "opportunities": {}, "people": {}}
    assembled = assemble_department_highlights(
        rows,
        report_date,
        "",
        names,
    )
    sections = list(assembled.get("sections") or [])
    sections.extend(load_leader_insight_sections(session, oauth_id, report_date))
    if not sections:
        return empty_leader_highlights_payload(report_date, subject_id=oauth_id)
    payload = department_highlights_payload({"sections": sections})
    payload["report_date"] = report_date.isoformat()
    payload["subject_id"] = oauth_id
    if payload["items"].get("leader_intervene"):
        try:
            from app.services.leader_intervention_actions import attach_item_interactions

            attach_item_interactions(session, [payload], report_date)
        except Exception as exc:
            logger.warning("拼今日需介入评论和任务失败: %s", exc)
    return payload


def _push_recipient(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    open_id = _text(raw.get("open_id"))
    platform = _text(raw.get("platform"))
    user_id = _text(raw.get("user_id") or raw.get("userId"))
    if not open_id or not platform or not user_id:
        return None
    return {
        "open_id": open_id,
        "name": _text(raw.get("name")) or "Unknown",
        "type": "department_manager",
        "department": raw.get("department"),
        "receive_id_type": raw.get("receive_id_type") or "open_id",
        "platform": platform,
        "user_id": user_id,
        "userId": user_id,
    }


def deliver_department_highlight_card(
    db_session: Session,
    recipient: Mapping[str, Any],
    assembled: Mapping[str, Any],
) -> dict[str, Any]:
    """飞书 / Lark 发无模板卡。不走会抄送运维的批量发送，也不进群。"""
    from app.services.platform_notification_service import platform_notification_service

    day = assembled.get("report_date")
    title = f"今日重点｜{day}"
    sections = render_card_sections(assembled)
    body = sections[0] if sections else ""
    extra = sections[1:]
    buttons = card_footer_buttons(assembled)
    platform = _text(recipient.get("platform"))
    if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
        card = platform_notification_service.build_feishu_markdown_card(
            body,
            title=title,
            extra_sections=extra or None,
            divide_extra_sections=bool(extra),
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

    text = render_dingtalk_markdown(assembled, title=title)
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
        "scene": SCENE_DEPARTMENT_HIGHLIGHTS,
        "variant": VARIANT_TODAY_HIGHLIGHTS,
        "report_date": report_date.isoformat(),
        "recipients_count": 0,
        "success_count": 0,
        "failed_recipients": [],
        "items": [],
    }


def _parent_department(chain: Sequence[tuple[str, str]] | Sequence[Sequence[str]]) -> Optional[tuple[str, str]]:
    """祖先链从自身到根。上一级是链上的第二项，只上探这一级。"""
    if len(chain) < 2:
        return None
    parent_id = _text(chain[1][0])
    parent_name = _text(chain[1][1])
    if not parent_id or not parent_name or parent_name == "未知部门":
        return None
    return parent_id, parent_name


def _pushable_recipients(raw_recipients: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [rec for rec in (_push_recipient(raw) for raw in raw_recipients) if rec]


def _skipped_department(department_id: str, reason: str) -> dict[str, Any]:
    return {
        "department_id": department_id,
        "sent": False,
        "skipped": True,
        "skip_reason": reason,
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


def _visit_only_leader_buckets(
    db_session: Session,
    report_date: date,
    department_id: str = "",
) -> dict[str, dict[str, Any]]:
    """没有需介入抽取的负责人，仍按有负责人的部门准备卡片（拜访对照或报告入口）。"""
    from app.repositories.department_mirror import department_mirror_repo
    from app.services.oauth_service import oauth_client
    from app.services.platform_notification_service import platform_notification_service

    allowed_name = ""
    if department_id:
        try:
            names = department_mirror_repo.get_department_names_by_ids(db_session, [department_id])
        except Exception:
            logger.exception("visit-only department name failed id=%s", department_id)
            return {}
        allowed_name = _text((names or {}).get(department_id))
        if not allowed_name:
            return {}
    try:
        oauth_map = oauth_client.get_departments_with_leaders() or {}
    except Exception:
        logger.exception("visit-only leader departments failed")
        return {}
    leaders: dict[str, dict[str, Any]] = {}
    for raw_name, raw_leaders in oauth_map.items():
        dept_name = _text(raw_name)
        if not dept_name or not raw_leaders:
            continue
        if allowed_name and dept_name != allowed_name:
            continue
        try:
            raw_recipients = platform_notification_service.resolve_department_report_recipients(
                db_session,
                dept_name,
            )
            dept_ids = list(department_mirror_repo.get_department_ids_by_name(db_session, dept_name) or [])
        except Exception:
            logger.exception("visit-only recipients failed department=%s", dept_name)
            continue
        recipients = _pushable_recipients(raw_recipients)
        if not recipients:
            continue
        kept = filter_opted_out_recipients(
            db_session,
            recipients,
            scene=SCENE_DEPARTMENT_HIGHLIGHTS,
            variant=VARIANT_TODAY_HIGHLIGHTS,
            department_ids=[_text(item) for item in dept_ids if _text(item)],
        )
        if not kept:
            continue
        dept_id = department_id or (_text(dept_ids[0]) if dept_ids else "")
        placeholder = {
            "report_date": report_date.isoformat(),
            "department_id": dept_id,
            "department_name": dept_name,
            "sections": [],
            "has_content": False,
        }
        for recipient in kept:
            bucket = leaders.setdefault(
                recipient["user_id"],
                {"recipient": recipient, "cards": []},
            )
            bucket["cards"].append(placeholder)
    return leaders


def handle_department_today_highlights(
    db_session: Session,
    *,
    report_date: Optional[date | str] = None,
) -> dict[str, Any]:
    """推送销售负责人今日重点。按负责人 oauth 一人一天一张卡，不进部门群。"""
    target = parse_report_date(report_date)
    result = _base_result(target)
    policy = load_report_push_policy()
    if not policy.variant_enabled(SCENE_DEPARTMENT_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS):
        logger.info("department today highlights disabled")
        return {**result, "skipped": True, "skip_reason": "variant_disabled"}
    quota_ok, quota_msg = check_today_highlights_quota("department")
    if not quota_ok:
        logger.warning("department today highlights blocked by billing: %s", quota_msg)
        return {**result, "skipped": True, "skip_reason": "billing_blocked"}

    rows = load_department_extract_rows(db_session, target)
    if rows is None:
        return {**result, "skipped": True, "skip_reason": "source_unavailable"}
    grouped = _group_by_subject(rows)

    from app.repositories.user_profile import user_profile_repo
    from app.services.sales_today_highlights import _recipient_from_profile

    names = load_display_names(db_session, rows)
    items: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    success_count = 0
    recipients_count = 0
    leaders: dict[str, dict[str, Any]] = {}

    for subject_id, subject_rows in grouped.items():
        assembled = assemble_department_highlights(
            subject_rows,
            target,
            "",
            names,
        )
        if not assembled["has_content"]:
            items.append(_skipped_department(subject_id, "empty_highlights"))
            continue
        profile = user_profile_repo.get_by_oauth_user_id(db_session, subject_id)
        recipient = _recipient_from_profile(profile)
        if recipient is None:
            items.append(_skipped_department(subject_id, "no_open_id"))
            continue
        dept_name = _text(getattr(profile, "department", None))
        if dept_name:
            assembled = {**assembled, "department_name": dept_name}
        kept = filter_opted_out_recipients(
            db_session,
            [recipient],
            scene=SCENE_DEPARTMENT_HIGHLIGHTS,
            variant=VARIANT_TODAY_HIGHLIGHTS,
            department_ids=[],
        )
        if not kept:
            items.append(_skipped_department(subject_id, "opted_out"))
            continue
        leaders[recipient["user_id"]] = {"recipient": kept[0], "cards": [assembled]}
    for user_id, bucket in _visit_only_leader_buckets(db_session, target, "").items():
        leaders.setdefault(user_id, bucket)

    for user_id, bucket in leaders.items():
        card = assemble_leader_highlights(bucket["cards"], target)
        department_names = [
            _text(item.get("department_name"))
            for item in bucket["cards"]
            if _text(item.get("department_name"))
        ]
        watch = load_leader_visit_watch(
            db_session,
            department_names,
            target,
            leader_user_id=user_id,
        )
        reports = load_leader_insight_reports(
            db_session,
            leader_oauth_id(db_session, user_id),
            target,
        )
        if not card.get("has_content") and not watch and not reports:
            items.append(
                {
                    "recipient_user_id": user_id,
                    "sent": False,
                    "skipped": True,
                    "skip_reason": "empty_highlights",
                }
            )
            continue
        if watch:
            card["visit_watch"] = watch
        if reports:
            card["report_entries"] = reports
        recipients_count += 1
        sent = deliver_department_highlight_card(db_session, bucket["recipient"], card)
        department_ids = list(card.get("department_ids") or [])
        item = {
            "recipient_user_id": user_id,
            "department_ids": department_ids,
            "sent": bool(sent.get("success")),
            "skipped": not sent.get("success"),
            "skip_reason": None if sent.get("success") else "send_failed",
        }
        if sent.get("success"):
            success_count += 1
            try:
                report_today_highlights_usage(
                    "department",
                    report_date=target,
                    user_id=user_id,
                )
            except Exception:
                logger.exception(
                    "department today highlights billing failed user_id=%s date=%s",
                    user_id,
                    target.isoformat(),
                )
        else:
            failed.extend(sent.get("failed_recipients") or [])
            if sent.get("message"):
                failed.append({"recipient_user_id": user_id, "message": sent["message"]})
        items.append(item)

    return {
        **result,
        "success": success_count > 0,
        "skipped": success_count == 0,
        "skip_reason": _aggregate_skip_reason(items, success_count),
        "recipients_count": recipients_count,
        "success_count": success_count,
        "failed_recipients": failed,
        "items": items,
    }

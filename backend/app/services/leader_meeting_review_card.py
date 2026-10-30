"""例会复盘卡片：按报告读取经营事项和员工待办，推给上传人。

列表和报告正文在 base-api。这里只读 crm_skill_extract_items。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from sqlmodel import Session, select

from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.services.leader_intervention_actions import interaction_fields
from app.services.sales_today_highlights import _recipient_from_profile, parse_report_date
from app.utils.date_utils import beijing_today_date
from app.utils.push_page_urls import build_leader_meeting_review_section_url

logger = logging.getLogger(__name__)

SUBJECT_USER = "USER"
STATUS_SUPERSEDED = "SUPERSEDED"
ITEM_EVENT = "MANAGEMENT_EVENT"
ITEM_TODO = "EMPLOYEE_TODO_CANDIDATE"
SECTION_EVENT = "management_events"
SECTION_TODO = "employee_todo_candidates"
TITLE_EVENT = "经营事项"
TITLE_TODO = "员工待办"

SECTIONS: tuple[tuple[str, str, str], ...] = (
    (SECTION_EVENT, ITEM_EVENT, TITLE_EVENT),
    (SECTION_TODO, ITEM_TODO, TITLE_TODO),
)


def _text(value: Any) -> str:
    if value is None:
        return ""
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


def _confidence(item: Mapping[str, Any]) -> float:
    try:
        return float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def _deleted(row: Any) -> bool:
    flag = _row_value(row, "is_deleted")
    return flag in (1, True, "1", "true", "True")


def empty_extracts(report_id: str) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "report_date": None,
        "items": {},
        "card_links": [],
        "can_manage": False,
    }


def _report_extract_statement(report_id: str):
    return select(CRMSkillExtractItem).where(
        CRMSkillExtractItem.source_report_unique_id == report_id,
        CRMSkillExtractItem.subject_type == SUBJECT_USER,
        CRMSkillExtractItem.is_deleted == 0,
        CRMSkillExtractItem.status != STATUS_SUPERSEDED,
        CRMSkillExtractItem.item_type.in_([ITEM_EVENT, ITEM_TODO]),
    )


def load_extract_rows(session: Session, report_id: str, owner_id: str) -> list[Any]:
    """读取这份报告挂在上传人身上的经营事项和员工待办。"""
    rid = _text(report_id)
    owner = _text(owner_id)
    if not rid or not owner:
        return []
    statement = _report_extract_statement(rid).where(CRMSkillExtractItem.subject_id == owner)
    return [
        row
        for row in session.exec(statement).all()
        if not _deleted(row)
    ]


def load_report_extract_rows(session: Session, report_id: str) -> list[Any]:
    """读取这份报告上的经营事项和员工待办，不按上传人收窄。"""
    rid = _text(report_id)
    if not rid:
        return []
    return [
        row
        for row in session.exec(_report_extract_statement(rid)).all()
        if not _deleted(row)
    ]


def _serialize_item(row: Any) -> dict[str, Any]:
    payload = _payload(row)
    item_type = _text(_row_value(row, "item_type"))
    data = {
        "unique_id": _text(_row_value(row, "unique_id")) or None,
        "claim": _text(payload.get("claim")),
        "confidence": payload.get("confidence"),
        "status": _text(_row_value(row, "status")) or None,
    }
    if item_type == ITEM_EVENT:
        data.update(
            {
                "business_object": _text(payload.get("business_object")),
                "management_issue": _text(payload.get("management_issue")),
                "change_summary": _text(payload.get("change_summary")),
                "leader_management": _text(payload.get("leader_management")),
            }
        )
    else:
        data.update(
            {
                "assignee_name": _text(payload.get("assignee_name")),
                "action": _text(payload.get("action")),
                "due_text": _text(payload.get("due_text")),
                "verification_criteria": _text(payload.get("verification_criteria")),
            }
        )
    data.update(interaction_fields(row))
    if item_type == ITEM_TODO:
        data["tasks"] = []
    return data


def _row_date(rows: Sequence[Any]) -> Optional[str]:
    for row in rows:
        raw = _row_value(row, "report_date")
        if isinstance(raw, date):
            return raw.isoformat()
        text = _text(raw)
        if text:
            return text[:10]
    return None


def resolve_report_date(requested: Any, rows: Sequence[Any]) -> date:
    """标题日期优先用请求里的业务日，否则用抽取行上的日期。"""
    if requested not in (None, ""):
        try:
            return parse_report_date(requested)
        except ValueError:
            pass
    raw = _row_date(rows)
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return beijing_today_date()


def assemble_extracts(rows: Sequence[Any], report_id: str, report_day: date) -> dict[str, Any]:
    """按类型分组，置信度高的在前。没有的类型不占位。"""
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in SECTIONS}
    types = {item_type: key for key, item_type, _ in SECTIONS}
    for row in rows:
        key = types.get(_text(_row_value(row, "item_type")))
        if not key:
            continue
        grouped[key].append(_serialize_item(row))
    items: dict[str, list[dict[str, Any]]] = {}
    links: list[dict[str, str]] = []
    day = report_day.isoformat()
    for key, _item_type, title in SECTIONS:
        section_items = sorted(grouped[key], key=_confidence, reverse=True)
        if not section_items:
            continue
        items[key] = section_items
        url = build_leader_meeting_review_section_url(report_id, day, key)
        link = {"key": key, "title": title}
        if url:
            link["url"] = url
        links.append(link)
    if not items:
        return empty_extracts(report_id)
    return {
        "report_id": report_id,
        "report_date": day,
        "items": items,
        "card_links": links,
    }


def extracts_for_viewer(
    session: Session,
    report_id: str,
    oauth_user_id: str,
    open_ids: Sequence[str] = (),
    *,
    company_manager: bool = False,
) -> dict[str, Any]:
    """上传人、其汇报链上级可以看和改。公司级管理人员只能看。对不上时返回空。"""
    rid = _text(report_id)
    viewer = _text(oauth_user_id)
    if not rid or (not viewer and not company_manager):
        return empty_extracts(rid)
    rows = load_report_extract_rows(session, rid)
    from app.services.leader_meeting_review_actions import attach_todo_tasks, select_visible_rows

    visible, can_manage = select_visible_rows(
        session,
        rows,
        viewer,
        open_ids,
        company_manager=company_manager,
    )
    if not visible:
        return empty_extracts(rid)
    assembled = assemble_extracts(visible, rid, resolve_report_date(None, visible))
    attach_todo_tasks(session, visible, assembled)
    assembled["can_manage"] = can_manage
    return assembled


def _event_lines(item: Mapping[str, Any]) -> list[str]:
    who = _text(item.get("business_object"))
    issue = _text(item.get("management_issue"))
    if who and issue:
        head = f"**{who}** · {issue}"
    elif who:
        head = f"**{who}**"
    else:
        head = f"**{issue}**" if issue else ""
    change = _text(item.get("change_summary")) or _text(item.get("claim"))
    action = _text(item.get("leader_management"))
    lines = [line for line in (head, change) if line]
    if action:
        if lines:
            lines.append("")
        lines.append(action)
    return lines


def _todo_lines(item: Mapping[str, Any]) -> list[str]:
    name = _text(item.get("assignee_name"))
    action = _text(item.get("action")) or _text(item.get("claim"))
    due = _text(item.get("due_text"))
    lines: list[str] = []
    if name:
        lines.append(f"**{name}**")
    if action:
        lines.append(action)
    if due:
        lines.append(f"截止 {due}")
    return lines


def _exit_link(total: int, url: str) -> str:
    if total <= 0 or not url:
        return ""
    extra = total - 1
    label = "查看" if extra <= 0 else f"更多（{extra}）"
    return f"[{label}]({url})"


def render_card_sections(assembled: Mapping[str, Any]) -> list[str]:
    """经营事项一块，员工待办一块。只展开置信度最高的一条。"""
    items = assembled.get("items") or {}
    urls = {_text(link.get("key")): _text(link.get("url")) for link in assembled.get("card_links") or []}
    sections: list[str] = []
    for key, _item_type, title in SECTIONS:
        rows = list(items.get(key) or [])
        if not rows:
            continue
        lines = [f"**{title}**"]
        body = _event_lines(rows[0]) if key == SECTION_EVENT else _todo_lines(rows[0])
        lines.extend(body)
        exit_link = _exit_link(len(rows), urls.get(key, ""))
        if exit_link:
            lines.append(exit_link)
        sections.append("\n".join(line for line in lines if line is not None))
    return [part for part in sections if part.strip()]


def render_dingtalk_markdown(assembled: Mapping[str, Any], *, title: str) -> str:
    lines = [f"# {title}"]
    for index, section in enumerate(render_card_sections(assembled)):
        if index:
            lines.append("")
        lines.append(section)
    return "\n".join(lines)


def deliver_card(db_session: Session, recipient: Mapping[str, Any], assembled: Mapping[str, Any]) -> dict[str, Any]:
    """发给上传人本人。不进群，不计费。"""
    from app.services.platform_notification_service import platform_notification_service

    title = f"例会复盘｜{assembled.get('report_date')}"
    sections = render_card_sections(assembled)
    body = sections[0] if sections else ""
    extra = sections[1:]
    platform = _text(recipient.get("platform"))
    if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
        card = platform_notification_service.build_feishu_markdown_card(
            body,
            title=title,
            extra_sections=extra or None,
            divide_extra_sections=bool(extra),
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


def _result(
    *,
    report_id: str,
    owner_id: str,
    report_date: Optional[str],
    pushed: bool,
    reason: Optional[str],
) -> dict[str, Any]:
    return {
        "success": True,
        "pushed": pushed,
        "reason": reason,
        "report_id": report_id,
        "owner_id": owner_id,
        "report_date": report_date,
    }


def handle_leader_meeting_review_card(
    db_session: Session,
    *,
    report_id: str,
    owner_id: str,
    report_date: Optional[str] = None,
) -> dict[str, Any]:
    """按报告抽取项给上传人发一张卡。没有抽取或不发出时 HTTP 仍由路由返回 200。"""
    rid = _text(report_id)
    owner = _text(owner_id)
    rows = load_extract_rows(db_session, rid, owner)
    if not rows:
        logger.info(
            "leader_meeting_review_card empty report_id=%s owner_id=%s",
            rid,
            owner,
        )
        return _result(
            report_id=rid,
            owner_id=owner,
            report_date=_text(report_date) or None,
            pushed=False,
            reason="empty_extracts",
        )
    day = resolve_report_date(report_date, rows)
    assembled = assemble_extracts(rows, rid, day)
    from app.repositories.user_profile import user_profile_repo

    profile = user_profile_repo.get_by_oauth_user_id(db_session, owner)
    recipient = _recipient_from_profile(profile)
    if recipient is None:
        return _result(
            report_id=rid,
            owner_id=owner,
            report_date=day.isoformat(),
            pushed=False,
            reason="no_open_id",
        )
    sent = deliver_card(db_session, recipient, assembled)
    if sent.get("success"):
        return _result(
            report_id=rid,
            owner_id=owner,
            report_date=day.isoformat(),
            pushed=True,
            reason=None,
        )
    logger.warning(
        "leader_meeting_review_card send failed report_id=%s owner_id=%s message=%s",
        rid,
        owner,
        sent.get("message"),
    )
    return _result(
        report_id=rid,
        owner_id=owner,
        report_date=day.isoformat(),
        pushed=False,
        reason="send_failed",
    )

"""例会复盘卡片：正文是会议总结，下面是抽取按钮，推给业务负责人和提交人。

列表和报告页仍由 base-api 提供。卡片读 bi_reports.report_content.summary，
抽取按钮读 crm_skill_extract_items。页面查询同时返回经营事项，待办候选在前。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.services.leader_intervention_actions import interaction_fields
from app.services.sales_today_highlights import _recipient_from_profile, parse_report_date
from app.services.today_highlights_format import CARD_LINK_SEPARATOR
from app.utils.date_utils import beijing_today_date
from app.utils.push_page_urls import (
    build_leader_meeting_review_report_url,
    build_leader_meeting_review_section_url,
)

logger = logging.getLogger(__name__)

SUBJECT_USER = "USER"
STATUS_SUPERSEDED = "SUPERSEDED"
ITEM_EVENT = "MANAGEMENT_EVENT"
ITEM_TODO = "EMPLOYEE_TODO_CANDIDATE"
SECTION_EVENT = "management_events"
SECTION_TODO = "employee_todo_candidates"
TITLE_EVENT = "经营事项"
TITLE_TODO = "待办候选"
FULL_REPORT_LABEL = "完整复盘报告"
DINGTALK_PARAGRAPH_GAP = "\u00a0"

SECTIONS: tuple[tuple[str, str, str], ...] = (
    (SECTION_TODO, ITEM_TODO, TITLE_TODO),
    (SECTION_EVENT, ITEM_EVENT, TITLE_EVENT),
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
        "uploader_user_id": None,
    }


def _report_extract_statement(report_id: str, item_types: Sequence[str]):
    return select(CRMSkillExtractItem).where(
        CRMSkillExtractItem.source_report_unique_id == report_id,
        CRMSkillExtractItem.subject_type == SUBJECT_USER,
        CRMSkillExtractItem.is_deleted == 0,
        CRMSkillExtractItem.status != STATUS_SUPERSEDED,
        CRMSkillExtractItem.item_type.in_(tuple(item_types)),
    )


def load_extract_rows(session: Session, report_id: str, owner_id: str) -> list[Any]:
    """读取这份报告挂在上传人身上的待办候选和经营事项，用来做卡片按钮。"""
    rid = _text(report_id)
    owner = _text(owner_id)
    if not rid or not owner:
        return []
    statement = _report_extract_statement(rid, (ITEM_TODO, ITEM_EVENT)).where(
        CRMSkillExtractItem.subject_id == owner
    )
    return [
        row
        for row in session.exec(statement).all()
        if not _deleted(row)
    ]


def load_report_extract_rows(session: Session, report_id: str) -> list[Any]:
    """读取这份报告上的待办候选和经营事项，不按上传人收窄。"""
    rid = _text(report_id)
    if not rid:
        return []
    return [
        row
        for row in session.exec(_report_extract_statement(rid, (ITEM_TODO, ITEM_EVENT))).all()
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
    """登录后都能看该报告的抽取。can_manage 对负责人、提交人或汇报链上级为 true。没有抽取时返回空。"""
    rid = _text(report_id)
    viewer = _text(oauth_user_id)
    if not rid:
        return empty_extracts(rid)
    rows = load_report_extract_rows(session, rid)
    from app.services.leader_meeting_review_actions import (
        attach_todo_tasks,
        local_user_id_for_oauth,
        select_visible_rows,
    )

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
    assembled["uploader_user_id"] = (
        local_user_id_for_oauth(session, getattr(visible[0], "subject_id", None)) or None
    )
    return assembled


def summary_from_report_content(raw: Any) -> str:
    """bi_reports.report_content 是 JSON。会议总结在 summary。"""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    parsed = raw if isinstance(raw, dict) else _parse_json(raw)
    if not isinstance(parsed, dict):
        return ""
    summary = parsed.get("summary")
    return summary.strip() if isinstance(summary, str) else ""


def load_report_summary(session: Session, report_id: str) -> str:
    """读取这份报告最新一行的会议总结。读不到时为空，不因此报错。"""
    rid = _text(report_id)
    if not rid:
        return ""
    try:
        found = session.execute(
            text(
                "SELECT report_content FROM bi_reports "
                "WHERE report_id = :report_id "
                "ORDER BY updated_at DESC, id DESC LIMIT 1"
            ),
            {"report_id": rid},
        ).first()
    except Exception as exc:
        logger.warning("例会复盘读取会议总结失败 report_id=%s: %s", rid, exc)
        return ""
    raw = found[0] if found is not None else None
    return summary_from_report_content(raw)


def _full_report_link(assembled: Mapping[str, Any]) -> str:
    """完整复盘报告。不带日期，打开这份报告页。"""
    url = build_leader_meeting_review_report_url(_text(assembled.get("report_id")))
    if not url:
        return ""
    return f"[{FULL_REPORT_LABEL}]({url})"


def extract_action_buttons(assembled: Mapping[str, Any]) -> list[tuple[str, str]]:
    """待办候选在前，经营事项在后。多于一条时按钮带上条数。没有链接的不放。"""
    items = assembled.get("items") or {}
    buttons: list[tuple[str, str]] = []
    for link in assembled.get("card_links") or []:
        if not isinstance(link, Mapping):
            continue
        key = _text(link.get("key"))
        title = _text(link.get("title"))
        url = _text(link.get("url"))
        count = len(list(items.get(key) or []))
        if not key or not title or not url or count <= 0:
            continue
        label = f"{title}（{count}）" if count > 1 else title
        buttons.append((label, url))
    return buttons


def feishu_card_parts(assembled: Mapping[str, Any]) -> tuple[str, str, list[tuple[str, str]]]:
    """飞书：正文是总结，接着是完整复盘报告，下面是抽取按钮。"""
    body = _text(assembled.get("summary")) or "--"
    return body, _full_report_link(assembled), extract_action_buttons(assembled)


def render_dingtalk_markdown(assembled: Mapping[str, Any], *, title: str) -> str:
    """钉钉没有按钮组件。结构和拜访轻量卡一样：总结、报告链接、抽取蓝链（同行用｜分隔）。"""
    body, report, buttons = feishu_card_parts(assembled)
    parts = [f"### {title}", DINGTALK_PARAGRAPH_GAP, body]
    if report:
        parts.extend([DINGTALK_PARAGRAPH_GAP, report])
    if buttons:
        extract_row = CARD_LINK_SEPARATOR.join(
            f"[{label}]({url})" for label, url in buttons
        )
        parts.extend([DINGTALK_PARAGRAPH_GAP, extract_row])
    return "\n".join(parts)


def deliver_card(db_session: Session, recipient: Mapping[str, Any], assembled: Mapping[str, Any]) -> dict[str, Any]:
    """发给上传人本人。不进群，不计费。"""
    from app.services.platform_notification_service import platform_notification_service

    title = f"例会复盘｜{assembled.get('report_date')}"
    body, report_link, buttons = feishu_card_parts(assembled)
    platform = _text(recipient.get("platform"))
    if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
        card = platform_notification_service.build_feishu_markdown_card(
            body,
            title=title,
            footer=report_link or None,
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
    creator_id: Optional[str] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "success": True,
        "pushed": pushed,
        "reason": reason,
        "report_id": report_id,
        "owner_id": owner_id,
        "report_date": report_date,
    }
    if creator_id:
        body["creator_id"] = creator_id
    return body


def _push_targets(owner_id: str, creator_id: Optional[str]) -> list[str]:
    targets: list[str] = []
    for raw in (owner_id, creator_id):
        text = _text(raw)
        if text and text not in targets:
            targets.append(text)
    return targets


def handle_leader_meeting_review_card(
    db_session: Session,
    *,
    report_id: str,
    owner_id: str,
    creator_id: Optional[str] = None,
    report_date: Optional[str] = None,
) -> dict[str, Any]:
    """按业务负责人读取抽取，给负责人和提交人各发一张同样的卡。

    抽取行挂在 owner_id 上。creator_id 缺省或与 owner_id 相同时只发一张。
    没有抽取或不发出时 HTTP 仍由路由返回 200。有一人发出则 pushed 为 true。
    """
    rid = _text(report_id)
    owner = _text(owner_id)
    creator = _text(creator_id) or None
    rows = load_extract_rows(db_session, rid, owner)
    summary = load_report_summary(db_session, rid)
    if not rows and not summary:
        logger.info(
            "leader_meeting_review_card empty report_id=%s owner_id=%s",
            rid,
            owner,
        )
        return _result(
            report_id=rid,
            owner_id=owner,
            creator_id=creator,
            report_date=_text(report_date) or None,
            pushed=False,
            reason="empty_extracts",
        )
    day = resolve_report_date(report_date, rows)
    assembled = assemble_extracts(rows, rid, day) if rows else empty_extracts(rid)
    assembled["report_id"] = rid
    assembled["report_date"] = day.isoformat()
    assembled["summary"] = summary
    if not summary and not assembled.get("items"):
        logger.info(
            "leader_meeting_review_card empty report_id=%s owner_id=%s",
            rid,
            owner,
        )
        return _result(
            report_id=rid,
            owner_id=owner,
            creator_id=creator,
            report_date=day.isoformat(),
            pushed=False,
            reason="empty_extracts",
        )
    from app.repositories.user_profile import user_profile_repo

    pushed_any = False
    saw_send_failed = False
    for target in _push_targets(owner, creator):
        profile = user_profile_repo.get_by_oauth_user_id(db_session, target)
        recipient = _recipient_from_profile(profile)
        if recipient is None:
            logger.info(
                "leader_meeting_review_card no open_id report_id=%s recipient=%s",
                rid,
                target,
            )
            continue
        sent = deliver_card(db_session, recipient, assembled)
        if sent.get("success"):
            pushed_any = True
            continue
        saw_send_failed = True
        logger.warning(
            "leader_meeting_review_card send failed report_id=%s recipient=%s message=%s",
            rid,
            target,
            sent.get("message"),
        )
    if pushed_any:
        reason = None
    elif saw_send_failed:
        reason = "send_failed"
    else:
        reason = "no_open_id"
    return _result(
        report_id=rid,
        owner_id=owner,
        creator_id=creator,
        report_date=day.isoformat(),
        pushed=pushed_any,
        reason=reason,
    )

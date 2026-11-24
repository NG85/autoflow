"""例会管理评价卡片：正文是洞察摘要，链接打开完整评价，底部按钮是结构化抽取。

收件人与 CXO 今日重点相同。不进群，不计费，不抄送。
洞察是 crm_entity_insight 的 LEADER_MEETING_BEHAVIOR_EVAL。
抽取是 crm_skill_extract_items 里 audience=cxo、来源等于这条洞察 unique_id 的行。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.crm_entity_insight import CRMEntityInsight
from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.services.company_today_highlights import resolve_company_highlight_recipients
from app.services.leader_meeting_review_card import (
    STATUS_SUPERSEDED,
    SUBJECT_USER,
    _confidence,
    _deleted,
    _row_value,
    _text,
    deliver_card,
)
from app.services.sales_today_highlights import parse_report_date
from app.utils.push_page_urls import (
    build_leader_meeting_cxo_report_url,
    build_leader_meeting_cxo_section_url,
    build_leader_meeting_review_report_url,
)

logger = logging.getLogger(__name__)

INSIGHT_TYPE = "LEADER_MEETING_BEHAVIOR_EVAL"
ENTITY_USER = "USER"
AUDIENCE_CXO = "cxo"
CARD_LINK_SEPARATOR = " ｜ "

SECTION_COACHING = "leader_behavior_coaching"
ITEM_COACHING = "LEADER_BEHAVIOR_COACHING"

SECTIONS: tuple[tuple[str, str, str], ...] = (
    (SECTION_COACHING, ITEM_COACHING, "管理方法建议"),
)


def _dedupe(values: Sequence[str]) -> list[str]:
    found: list[str] = []
    for raw in values:
        text = _text(raw)
        if text and text not in found:
            found.append(text)
    return found


def _sort_key(row: Any) -> tuple[datetime, int]:
    generated = _row_value(row, "generated_at")
    if not isinstance(generated, datetime):
        generated = datetime.min
    row_id = _row_value(row, "id")
    try:
        number = int(row_id or 0)
    except (TypeError, ValueError):
        number = 0
    return generated, number


def _live_insight_statement():
    """人员身上、未删除、未过期的管理评价。"""
    return (
        select(CRMEntityInsight)
        .where(CRMEntityInsight.entity_type == ENTITY_USER)
        .where(CRMEntityInsight.insight_type == INSIGHT_TYPE)
        .where(CRMEntityInsight.is_deleted == 0)
        .where(
            or_(
                CRMEntityInsight.expires_at.is_(None),
                CRMEntityInsight.expires_at > func.utc_timestamp(),
            )
        )
    )


def load_latest_insight(session: Session, insight_ids: Sequence[str]) -> Optional[Any]:
    """在传入的洞察里取最新一条管理评价。类型不对、已删除或已过期的不读。"""
    ids = _dedupe(insight_ids)
    if not ids:
        return None
    statement = _live_insight_statement().where(CRMEntityInsight.unique_id.in_(ids))
    rows = [row for row in session.exec(statement).all() if not _deleted(row)]
    if not rows:
        return None
    return max(rows, key=_sort_key)


def load_cxo_extract_rows(session: Session, insight_id: str) -> list[Any]:
    """读取挂在这条洞察上的 CXO 抽取。不按被评价人再收窄。"""
    iid = _text(insight_id)
    if not iid:
        return []
    item_types = tuple(item_type for _key, item_type, _title in SECTIONS)
    statement = select(CRMSkillExtractItem).where(
        CRMSkillExtractItem.source_report_unique_id == iid,
        CRMSkillExtractItem.subject_type == SUBJECT_USER,
        CRMSkillExtractItem.audience == AUDIENCE_CXO,
        CRMSkillExtractItem.is_deleted == 0,
        CRMSkillExtractItem.status != STATUS_SUPERSEDED,
        CRMSkillExtractItem.item_type.in_(item_types),
    )
    return [row for row in session.exec(statement).all() if not _deleted(row)]


def _serialize_item(row: Any) -> dict[str, Any]:
    from app.services.leader_intervention_actions import interaction_fields

    payload = _row_value(row, "payload")
    body = payload if isinstance(payload, Mapping) else {}
    data = {
        "unique_id": _text(_row_value(row, "unique_id")) or None,
        "claim": _text(body.get("claim")),
        "why": _text(body.get("why")),
        "confidence": body.get("confidence"),
    }
    data.update(interaction_fields(row))
    data["tasks"] = []
    return data


def _report_day(requested: Any) -> Optional[date]:
    """卡片标题上的日期。只认请求里的 report_date，不用来定位评价报告。"""
    if requested in (None, ""):
        return None
    try:
        return parse_report_date(requested)
    except ValueError:
        return None


def assemble_cxo_card(
    insight: Any,
    rows: Sequence[Any],
    *,
    report_date: Optional[str] = None,
    source_report_id: Optional[str] = None,
) -> dict[str, Any]:
    """摘要进正文。完整评价和原复盘是链接。抽取只做按钮。"""
    insight_id = _text(_row_value(insight, "unique_id"))
    day = _report_day(report_date)
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in SECTIONS}
    types = {item_type: key for key, item_type, _ in SECTIONS}
    for row in rows:
        key = types.get(_text(_row_value(row, "item_type")))
        if not key:
            continue
        grouped[key].append(_serialize_item(row))

    items: dict[str, list[dict[str, Any]]] = {}
    links: list[dict[str, str]] = []
    for key, _item_type, title in SECTIONS:
        section_items = sorted(grouped[key], key=_confidence, reverse=True)
        if not section_items:
            continue
        items[key] = section_items
        url = build_leader_meeting_cxo_section_url(insight_id, key)
        link = {"key": key, "title": title}
        if url:
            link["url"] = url
        links.append(link)

    summary = _text(_row_value(insight, "summary"))
    detail = _text(_row_value(insight, "detail_text"))
    source_id = _text(source_report_id)
    report_url = (
        build_leader_meeting_cxo_report_url(source_id) if detail and source_id else ""
    )
    source_url = ""
    if source_id:
        source_url = build_leader_meeting_review_report_url(source_id)
    footer_parts = []
    if report_url:
        footer_parts.append(f"[完整评价报告]({report_url})")
    if source_url:
        footer_parts.append(f"[原复盘报告]({source_url})")
    return {
        "insight_id": insight_id,
        "report_id": insight_id,
        "report_date": day.isoformat() if day else None,
        "summary": summary,
        "detail_text": detail,
        "items": items,
        "card_links": links,
        "can_manage": False,
        "card_title": f"例会管理评价｜{day.isoformat()}" if day else "例会管理评价",
        "report_link": CARD_LINK_SEPARATOR.join(footer_parts),
        "report_url": report_url,
        "source_report_url": source_url,
        "wecom_desc": "例会管理评价",
        "wecom_jump_label": "完整评价报告",
    }


def empty_cxo_extracts(insight_id: str) -> dict[str, Any]:
    return {
        "insight_id": _text(insight_id) or None,
        "report_date": None,
        "items": {},
        "card_links": [],
        "can_manage": False,
        "uploader_user_id": None,
    }


def _extract_report_date(rows: Sequence[Any]) -> Optional[str]:
    for row in rows:
        raw = _row_value(row, "report_date")
        if hasattr(raw, "isoformat"):
            return raw.isoformat()
        text = _text(raw)
        if text:
            return text[:10]
    return None


def cxo_extracts_for_viewer(
    session: Session,
    insight_id: str,
    oauth_user_id: str = "",
    open_ids: Sequence[str] = (),
    viewer_user_id: str = "",
) -> Optional[dict[str, Any]]:
    """CXO 查看这条评价上的抽取。全文不在这里。没有抽取时返回空。

    只有 CXO 今日重点的接收人能看。被评价的人若也是 CXO，同样能看。
    能看就能改。赞踩、采纳、拒绝、挂任务和评论复用例会复盘的写入接口。
    无权时返回 None。
    """
    iid = _text(insight_id)
    if not iid:
        return None
    from app.services.leader_meeting_review_actions import (
        attach_todo_tasks,
        viewer_can_read_cxo_extracts,
    )

    rows = load_cxo_extract_rows(session, iid)
    if not viewer_can_read_cxo_extracts(
        session,
        rows,
        user_id=viewer_user_id,
        oauth_id=_text(oauth_user_id),
        open_ids=open_ids,
    ):
        return None
    if not rows:
        return empty_cxo_extracts(iid)
    day = _extract_report_date(rows)
    assembled = assemble_cxo_card(
        {"unique_id": iid, "summary": "", "detail_text": "", "period_key": day or ""},
        rows,
        report_date=day,
    )
    attach_todo_tasks(session, rows, assembled)
    return {
        "insight_id": iid,
        "report_date": day,
        "items": assembled["items"],
        "card_links": assembled["card_links"],
        "can_manage": True,
        "uploader_user_id": None,
    }


def owner_name_for_insight(session: Session, insight: Any) -> str:
    """原例会负责人姓名。洞察挂在负责人的 oauth id 上。查不到名字时留空。"""
    oauth_id = _text(_row_value(insight, "entity_id"))
    if not oauth_id or session is None:
        return ""
    from app.repositories.user_profile import user_profile_repo

    profile = user_profile_repo.get_by_oauth_user_id(session, oauth_id)
    if profile is None:
        return ""
    return _text(getattr(profile, "name", None))


def with_owner_name(assembled: Mapping[str, Any], owner_name: str) -> dict[str, Any]:
    """标题和正文写上负责人，方便 CXO 看出评价的是谁。"""
    card = dict(assembled)
    name = _text(owner_name)
    if not name:
        return card
    card["owner_name"] = name
    day = _text(card.get("report_date"))
    card["card_title"] = f"例会管理评价｜{name}｜{day}" if day else f"例会管理评价｜{name}"
    summary = _text(card.get("summary"))
    card["summary"] = f"负责人：{name}" + (f"\n\n{summary}" if summary else "")
    return card


def card_has_content(assembled: Mapping[str, Any]) -> bool:
    if _text(assembled.get("summary")) or _text(assembled.get("detail_text")):
        return True
    return bool(assembled.get("items"))


def _result(
    *,
    insight_id: Optional[str],
    report_date: Optional[str],
    pushed: bool,
    reason: Optional[str],
    recipient_user_ids: Sequence[str],
    source_report_id: Optional[str] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "success": True,
        "pushed": pushed,
        "reason": reason,
        "insight_id": insight_id,
        "report_date": report_date,
        "recipient_user_ids": list(recipient_user_ids),
    }
    if source_report_id:
        body["source_report_id"] = source_report_id
    return body


def _recipient_ids(recipients: Sequence[Mapping[str, Any]]) -> list[str]:
    found: list[str] = []
    for recipient in recipients:
        user_id = _text(recipient.get("user_id") or recipient.get("userId"))
        if user_id and user_id not in found:
            found.append(user_id)
    return found


def handle_leader_meeting_review_cxo_card(
    db_session: Session,
    *,
    insight_ids: Sequence[str],
    recipient_user_ids: Optional[Sequence[str]] = None,
    report_date: Optional[str] = None,
    source_report_id: Optional[str] = None,
) -> dict[str, Any]:
    """给 CXO 今日重点的同一批人各发一张例会管理评价卡。

    开关关闭时不查人、不发送。请求里的本地 user_id 只能收窄这批人。
    没有可读的评价时 HTTP 仍由路由返回 200。有一人发出则 pushed 为 true。
    """
    ids = _dedupe(insight_ids)
    source_id = _text(source_report_id) or None
    if not settings.LEADER_MEETING_CXO_CARD_PUSH_ENABLED:
        logger.info("leader_meeting_review_cxo_card disabled ids=%s", ids)
        return _result(
            insight_id=ids[0] if ids else None,
            report_date=_text(report_date) or None,
            pushed=False,
            reason="feature_disabled",
            recipient_user_ids=[],
            source_report_id=source_id,
        )
    recipients = resolve_company_highlight_recipients(db_session, recipient_user_ids)
    if recipients is None:
        logger.info("leader_meeting_review_cxo_card no recipients ids=%s", ids)
        return _result(
            insight_id=None,
            report_date=_text(report_date) or None,
            pushed=False,
            reason="no_recipients",
            recipient_user_ids=[],
            source_report_id=source_id,
        )
    target_ids = _recipient_ids(recipients)
    insight = load_latest_insight(db_session, ids)
    if insight is None:
        logger.info("leader_meeting_review_cxo_card insight missing ids=%s", ids)
        return _result(
            insight_id=None,
            report_date=_text(report_date) or None,
            pushed=False,
            reason="insight_not_found",
            recipient_user_ids=target_ids,
            source_report_id=source_id,
        )
    insight_id = _text(_row_value(insight, "unique_id"))
    rows = load_cxo_extract_rows(db_session, insight_id)
    assembled = assemble_cxo_card(
        insight,
        rows,
        report_date=report_date,
        source_report_id=source_id,
    )
    if not card_has_content(assembled):
        logger.info("leader_meeting_review_cxo_card empty insight_id=%s", insight_id)
        return _result(
            insight_id=insight_id,
            report_date=assembled["report_date"],
            pushed=False,
            reason="empty_extracts",
            recipient_user_ids=target_ids,
            source_report_id=source_id,
        )
    assembled = with_owner_name(assembled, owner_name_for_insight(db_session, insight))
    if not recipients:
        logger.info("leader_meeting_review_cxo_card no open_id insight_id=%s", insight_id)
        return _result(
            insight_id=insight_id,
            report_date=assembled["report_date"],
            pushed=False,
            reason="no_open_id",
            recipient_user_ids=[],
            source_report_id=source_id,
        )

    pushed_any = False
    saw_send_failed = False
    for recipient in recipients:
        sent = deliver_card(db_session, recipient, assembled)
        if sent.get("success"):
            pushed_any = True
            continue
        saw_send_failed = True
        logger.warning(
            "leader_meeting_review_cxo_card send failed insight_id=%s recipient=%s message=%s",
            insight_id,
            _text(recipient.get("user_id") or recipient.get("userId")),
            sent.get("message"),
        )
    if pushed_any:
        reason = None
    elif saw_send_failed:
        reason = "send_failed"
    else:
        reason = "no_open_id"
    return _result(
        insight_id=insight_id,
        report_date=assembled["report_date"],
        pushed=pushed_any,
        reason=reason,
        recipient_user_ids=target_ids,
        source_report_id=source_id,
    )

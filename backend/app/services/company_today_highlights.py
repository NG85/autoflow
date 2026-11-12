"""CXO 今日重点：一张卡，展示公司人效和重点商机盘点。哪天发由调度配置决定。

收件人优先用 company_highlights.recipient_user_ids（本地 user_id）。
名单没配时，改走公司日报接收人（notification:daily_report_company:receive）。
洞察由 Aldebaran 写入 crm_entity_insight：entity_type=DEPARTMENT，
period_key 等于推送日（业务日），与其他今日重点相同。公司级 entity_id 为 __COMPANY__。
同一张卡发给全部接收人，不进群，不抄送。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.models.crm_entity_insight import CRMEntityInsight
from app.platforms.constants import (
    PLATFORM_DINGTALK,
    PLATFORM_FEISHU,
    PLATFORM_LARK,
    PLATFORM_WECOM,
)
from app.policies.review_session_access import COMPANY_CXO_DEPARTMENT_ID
from app.services.leader_insight_reports import report_entry_lines
from app.services.notification_delivery_preference import filter_opted_out_recipients
from app.services.notification_scene_catalog import (
    SCENE_COMPANY_HIGHLIGHTS,
    VARIANT_TODAY_HIGHLIGHTS,
)
from app.services.report_push_policy import load_report_push_policy
from app.services.sales_today_highlights import parse_report_date
from app.services.today_highlights_billing import (
    check_today_highlights_quota,
    report_today_highlights_usage,
)
from app.utils.push_page_urls import build_company_highlights_url

logger = logging.getLogger(__name__)

ENTITY_DEPARTMENT = "DEPARTMENT"
COMPANY_SCOPE_NAME = "公司"
CXO_CARD_TITLE = "CXO今日重点"
INSIGHT_EFFICIENCY = "CXO_EFFICIENCY_INSIGHT"
INSIGHT_KEY_DEAL = "CXO_KEY_DEAL_REVIEW"


@dataclass(frozen=True)
class CxoReportSlot:
    key: str
    title: str
    insight_type: str


# 卡片和页面顺序：公司人效，然后重点商机盘点。没有匹配洞察的槽位不出现。
CXO_REPORT_SLOTS: tuple[CxoReportSlot, ...] = (
    CxoReportSlot(
        key="cxo_efficiency_insight",
        title="公司人效",
        insight_type=INSIGHT_EFFICIENCY,
    ),
    CxoReportSlot(
        key="cxo_key_deal_review",
        title="重点商机盘点",
        insight_type=INSIGHT_KEY_DEAL,
    ),
)


def insight_period_key(report_date: date) -> str:
    """period_key 等于业务日，也就是推送日。"""
    return report_date.isoformat()


def render_card_markdown(assembled: Mapping[str, Any]) -> str:
    lines = report_entry_lines(assembled.get("report_entries"))
    return lines[0] if lines else ""


def render_dingtalk_markdown(assembled: Mapping[str, Any], *, title: str) -> str:
    body = render_card_markdown(assembled)
    if not body:
        return f"# {title}"
    return f"# {title}\n\n{body}"


def pick_report_sections(
    rows: Sequence[Any],
    *,
    push_day: date,
    names: Mapping[str, str],
    slots: Sequence[CxoReportSlot] = CXO_REPORT_SLOTS,
) -> list[dict[str, Any]]:
    """每个槽位收集当天全部部门的最新一条。公司级排在部门前面。"""
    period_key = insight_period_key(push_day)
    day = push_day.isoformat()
    ordered = _newest_first(rows)
    sections: list[dict[str, Any]] = []
    for slot in slots:
        picked = _latest_by_entity(ordered, slot.insight_type, period_key)
        items = [
            _serialize_item(row, names)
            for row in _sorted_rows(picked, names)
            if _has_body(row)
        ]
        if not items:
            continue
        sections.append(
            {
                "key": slot.key,
                "title": slot.title,
                "item_type": slot.insight_type,
                "items": items,
                "url": build_company_highlights_url(day, slot.key),
            }
        )
    return sections


def report_entries_from_sections(sections: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {"key": _text(section.get("key")), "label": _text(section.get("title")), "url": url}
        for section in sections
        if (url := _text(section.get("url"))) and _text(section.get("title"))
    ]


def highlights_payload(
    sections: Sequence[Mapping[str, Any]],
    *,
    report_date: date,
) -> dict[str, Any]:
    items: dict[str, list[dict[str, Any]]] = {}
    links: list[dict[str, Any]] = []
    for section in sections:
        full_items = [dict(item) for item in (section.get("items") or []) if isinstance(item, Mapping)]
        key = _text(section.get("key"))
        if not key or not full_items:
            continue
        items[key] = full_items
        url = _text(section.get("url"))
        links.append(
            {
                "key": key,
                "title": _text(section.get("title")) or key,
                "count": len(full_items),
                "url": url or None,
                "item_types": [_text(section.get("item_type")) or key],
            }
        )
    return {
        "report_date": report_date.isoformat(),
        "items": items,
        "card_links": links,
    }


def empty_highlights_payload(report_date: date) -> dict[str, Any]:
    return highlights_payload([], report_date=report_date)


def highlights_for_viewer(
    session: Session,
    user_id: Any,
    report_date: date,
) -> dict[str, Any]:
    """高管角色，或这次的推送接收人，才返回正文。其他人是空页。"""
    if not _viewer_can_read(session, user_id):
        return empty_highlights_payload(report_date)
    rows = load_cxo_insight_rows(session, report_date) or []
    names = load_department_names(session, rows)
    sections = pick_report_sections(rows, push_day=report_date, names=names)
    return highlights_payload(sections, report_date=report_date)


def load_cxo_insight_rows(session: Session, report_date: date) -> Optional[list[Any]]:
    """按推送日读取两个 CXO 洞察。查库失败返回 None。"""
    period_key = insight_period_key(report_date)
    types = [slot.insight_type for slot in CXO_REPORT_SLOTS]
    try:
        statement = (
            select(CRMEntityInsight)
            .where(CRMEntityInsight.entity_type == ENTITY_DEPARTMENT)
            .where(CRMEntityInsight.insight_type.in_(types))
            .where(CRMEntityInsight.period_key == period_key)
            .where(CRMEntityInsight.is_deleted == 0)
            .where(
                or_(
                    CRMEntityInsight.expires_at.is_(None),
                    CRMEntityInsight.expires_at > func.utc_timestamp(),
                )
            )
            .order_by(col(CRMEntityInsight.generated_at).desc(), col(CRMEntityInsight.id).desc())
        )
        return list(session.exec(statement).all())
    except Exception:
        logger.exception("cxo today highlights insight load failed")
        return None


def load_department_names(session: Session, rows: Sequence[Any]) -> dict[str, str]:
    ids = [
        entity_id
        for row in rows
        if (entity_id := _text(_row_value(row, "entity_id")))
        and entity_id != COMPANY_CXO_DEPARTMENT_ID
    ]
    if not ids:
        return {}
    try:
        from app.repositories.department_mirror import department_mirror_repo

        found = department_mirror_repo.get_department_names_by_ids(session, ids) or {}
    except Exception:
        logger.exception("cxo today highlights department names failed")
        return {}
    return {_text(key): _text(value) for key, value in found.items() if _text(key) and _text(value)}


def deliver_company_highlight_card(
    db_session: Session,
    recipient: Mapping[str, Any],
    assembled: Mapping[str, Any],
) -> dict[str, Any]:
    """飞书 / Lark 发无模板卡。不抄送，不进群。"""
    from app.services.platform_notification_service import platform_notification_service

    day = assembled.get("report_date")
    title = f"{CXO_CARD_TITLE}｜{day}"
    platform = _text(recipient.get("platform"))
    if platform in (PLATFORM_FEISHU, PLATFORM_LARK):
        body = render_card_markdown(assembled)
        card = platform_notification_service.build_feishu_markdown_card(body, title=title)
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

    if platform == PLATFORM_WECOM:
        from app.services.today_highlights_wecom import build_cxo_wecom_template_card

        card = build_cxo_wecom_template_card(assembled)
        tokens = platform_notification_service._get_platform_tokens([platform])
        if platform not in tokens:
            return {"success": False, "message": "wecom push is not configured"}
        chatid = _text(recipient.get("open_id"))
        if not chatid:
            return {"success": False, "message": "recipient has no oauth open_id"}
        try:
            platform_notification_service._send_message(
                chatid,
                tokens[platform],
                card,
                platform,
                receive_id_type=_text(recipient.get("receive_id_type")) or "open_id",
                msg_type="template_card",
            )
            return {"success": True, "message": None, "failed_recipients": []}
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc),
                "failed_recipients": [
                    {
                        "open_id": chatid,
                        "name": recipient.get("name"),
                        "platform": platform,
                        "error": str(exc),
                    }
                ],
            }

    text = render_dingtalk_markdown(assembled, title=title)
    if platform == PLATFORM_DINGTALK:
        return platform_notification_service.send_platform_notification(
            db_session,
            recipient_user_id=_text(recipient.get("user_id")),
            content=text or title,
            content_type="markdown",
            title=title,
            delivery="card",
        )
    return {"success": False, "message": "unsupported platform"}


def handle_company_today_highlights(
    db_session: Session,
    *,
    report_date: Optional[date | str] = None,
    recipient_user_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """推送 CXO 今日重点。不按星期拒绝，全体接收人同一张卡。

    recipient_user_ids 是可选本地 user_id，只能从实际收件人里收窄，不能扩大名单。
    策略没写 recipient_user_ids 时，收件人与公司日报相同。
    """
    target = parse_report_date(report_date)
    result = _base_result(target)

    policy = load_report_push_policy()
    if not policy.variant_enabled(SCENE_COMPANY_HIGHLIGHTS, VARIANT_TODAY_HIGHLIGHTS):
        logger.info("cxo today highlights disabled")
        return {**result, "skipped": True, "skip_reason": "variant_disabled"}

    quota_ok, quota_msg = check_today_highlights_quota("company")
    if not quota_ok:
        logger.warning("cxo today highlights blocked by billing: %s", quota_msg)
        return {**result, "skipped": True, "skip_reason": "billing_blocked"}

    rows = load_cxo_insight_rows(db_session, target)
    if rows is None:
        return {**result, "skipped": True, "skip_reason": "source_unavailable"}
    names = load_department_names(db_session, rows)
    sections = pick_report_sections(rows, push_day=target, names=names)
    entries = report_entries_from_sections(sections)
    if not entries:
        return {**result, "skipped": True, "skip_reason": "empty_highlights"}

    recipients = _resolve_recipients(db_session, recipient_user_ids)
    if recipients is None:
        return {**result, "skipped": True, "skip_reason": "no_recipients"}
    if not recipients:
        return {**result, "skipped": True, "skip_reason": "no_open_id"}
    kept = filter_opted_out_recipients(
        db_session,
        recipients,
        scene=SCENE_COMPANY_HIGHLIGHTS,
        variant=VARIANT_TODAY_HIGHLIGHTS,
        department_ids=[""],
    )
    if not kept:
        return {**result, "skipped": True, "skip_reason": "opted_out"}

    assembled = {
        "report_date": target.isoformat(),
        "sections": sections,
        "report_entries": entries,
    }
    success_count = 0
    failed: list[dict[str, Any]] = []
    sent_user_ids: list[str] = []
    for recipient in kept:
        user_id = _text(recipient.get("user_id"))
        sent = deliver_company_highlight_card(db_session, recipient, assembled)
        if sent.get("success"):
            success_count += 1
            if user_id and user_id not in sent_user_ids:
                sent_user_ids.append(user_id)
            try:
                report_today_highlights_usage(
                    "company",
                    report_date=target,
                    user_id=user_id,
                )
            except Exception:
                logger.exception(
                    "cxo today highlights billing failed user_id=%s date=%s",
                    user_id,
                    target.isoformat(),
                )
        else:
            failed.extend(sent.get("failed_recipients") or [])
            if sent.get("message"):
                failed.append({"recipient_user_id": user_id, "message": sent["message"]})

    return {
        **result,
        "success": success_count > 0,
        "skipped": success_count == 0,
        "skip_reason": None if success_count else "send_failed",
        "recipients_count": len(kept),
        "success_count": success_count,
        "failed_recipients": failed,
        "sent_user_ids": sent_user_ids,
    }


def _base_result(report_date: date) -> dict[str, Any]:
    return {
        "success": False,
        "skipped": False,
        "skip_reason": None,
        "scene": SCENE_COMPANY_HIGHLIGHTS,
        "variant": VARIANT_TODAY_HIGHLIGHTS,
        "report_date": report_date.isoformat(),
        "recipients_count": 0,
        "success_count": 0,
        "failed_recipients": [],
        "sent_user_ids": [],
    }


def _policy_recipient_ids() -> list[str]:
    return load_report_push_policy().override_user_ids(
        SCENE_COMPANY_HIGHLIGHTS,
        VARIANT_TODAY_HIGHLIGHTS,
    )


def _viewer_can_read(session: Session, user_id: Any) -> bool:
    """biz_admin、sys_admin、executive 能看。推送接收人从卡片进来也能看。"""
    if _viewer_has_executive_role(user_id):
        return True
    return _viewer_is_recipient(session, user_id)


def _viewer_has_executive_role(user_id: Any) -> bool:
    from app.services.leader_meeting_review_actions import viewer_is_company_manager

    return viewer_is_company_manager(user_id)


def _viewer_is_recipient(session: Session, user_id: Any) -> bool:
    uid = _text(user_id)
    if not uid:
        return False
    allowed = _policy_recipient_ids()
    if allowed:
        return uid in set(allowed)
    from app.services.platform_notification_service import platform_notification_service

    recipients = platform_notification_service.get_recipients_for_company_daily_report(session)
    return any(_recipient_user_id(item) == uid for item in recipients)


def _resolve_recipients(
    db_session: Session,
    recipient_user_ids: Optional[Sequence[str]],
) -> Optional[list[dict[str, Any]]]:
    """None 表示没有可发的人。空列表表示名单里的人都没有 open_id。

    策略写了 recipient_user_ids 就只用这份名单。没写时走公司日报接收人。
    请求里的 recipient_user_ids 只能在这一批人里收窄。
    """
    from app.services.platform_notification_service import platform_notification_service

    policy_ids = _policy_recipient_ids()
    wanted = None
    if recipient_user_ids is not None:
        wanted = {_text(item) for item in recipient_user_ids if _text(item)}
    if policy_ids:
        if wanted is not None:
            policy_ids = [item for item in policy_ids if item in wanted]
        if not policy_ids:
            return None
        return platform_notification_service.recipients_from_user_ids(
            db_session,
            policy_ids,
            recipient_type="named_recipient",
        )
    recipients = platform_notification_service.get_recipients_for_company_daily_report(db_session)
    if wanted is not None:
        recipients = [item for item in recipients if _recipient_user_id(item) in wanted]
    if not recipients:
        return None
    return list(recipients)


def _recipient_user_id(recipient: Mapping[str, Any]) -> str:
    return _text(recipient.get("user_id") or recipient.get("userId"))


def _newest_first(rows: Sequence[Any]) -> list[Any]:
    def sort_key(row: Any) -> tuple[str, int]:
        stamp = _row_value(row, "generated_at")
        if hasattr(stamp, "isoformat"):
            stamp = stamp.isoformat()
        try:
            ident = int(_row_value(row, "id") or 0)
        except (TypeError, ValueError):
            ident = 0
        return (_text(stamp), ident)

    return sorted(rows, key=sort_key, reverse=True)


def _latest_by_entity(rows: Sequence[Any], insight_type: str, period_key: str) -> list[Any]:
    wanted = insight_type.upper()
    seen: set[str] = set()
    picked: list[Any] = []
    for row in rows:
        entity_type = _text(_row_value(row, "entity_type")).upper()
        if entity_type and entity_type != ENTITY_DEPARTMENT:
            continue
        if _text(_row_value(row, "insight_type")).upper() != wanted:
            continue
        if _text(_row_value(row, "period_key")) != period_key:
            continue
        entity_id = _text(_row_value(row, "entity_id"))
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        picked.append(row)
    return picked


def _sorted_rows(rows: Sequence[Any], names: Mapping[str, str]) -> list[Any]:
    def sort_key(row: Any) -> tuple[int, str, str]:
        entity_id = _text(_row_value(row, "entity_id"))
        company = 0 if entity_id == COMPANY_CXO_DEPARTMENT_ID else 1
        return (company, _scope_name(entity_id, names), entity_id)

    return sorted(rows, key=sort_key)


def _serialize_item(row: Any, names: Mapping[str, str]) -> dict[str, Any]:
    entity_id = _text(_row_value(row, "entity_id"))
    summary = _text(_row_value(row, "summary"))
    detail = _text(_row_value(row, "detail_text"))
    return {
        "unique_id": _text(_row_value(row, "unique_id")) or None,
        "summary": summary or None,
        "detail_text": detail or None,
        "department_id": entity_id or None,
        "department_name": _scope_name(entity_id, names),
    }


def _scope_name(entity_id: str, names: Mapping[str, str]) -> str:
    if entity_id == COMPANY_CXO_DEPARTMENT_ID:
        return COMPANY_SCOPE_NAME
    return _text(names.get(entity_id)) or entity_id


def _has_body(row: Any) -> bool:
    return bool(_text(_row_value(row, "summary")) or _text(_row_value(row, "detail_text")))


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

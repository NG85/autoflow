"""Aldebaran crm_postvisit_extract_items：销售视角复盘结构化抽取。

抽取正文由 Aldebaran 写入。查询和赞踩、采纳/拒绝都走 CRMPostvisitExtractItem。查询失败返回空结果，不抬 500。
赞踩写入 feedback 四列，采纳/拒绝写入 status、adopted_todo_id、reject_reason。
行上只保留最新一次交互，变更前后写入应用日志。
查询只返回当前用户自己的赞或踩；采纳/拒绝是条目状态，随抽取一起返回。
card_links 以抽取行上的 YAML 快照为准；CARD_LINKS_BY_PROFILE 仅作无快照时的兜底。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlmodel import Session, select

from app.models.crm_postvisit_extract_items import CRMPostvisitExtractItem

logger = logging.getLogger(__name__)

EXTRACT_STATUS_PENDING = "PENDING"
EXTRACT_STATUS_SUPERSEDED = "SUPERSEDED"
EXTRACT_STATUS_ADOPTED = "ADOPTED"
EXTRACT_STATUS_REJECTED = "REJECTED"
VIEWER_ROLE_SALES = "sales"
DEFAULT_PROFILE_ID = "postvisit_sales_v1_0"
FEEDBACK_UP = "up"
FEEDBACK_DOWN = "down"
DECISION_ADOPT = "adopt"
DECISION_REJECT = "reject"
_FEEDBACK_COMMENT_MAX = 500
_REJECT_REASON_MAX = 1000
_DECISION_BATCH_MAX = 100

# 仅当行上没有 card_links 快照时使用（历史数据 / 列尚未加上）。
# 新抽取以 Aldebaran sales_extract.yaml 落库快照为准，不要在这里改入口。
CARD_LINKS_BY_PROFILE: dict[str, list[dict[str, Any]]] = {
    DEFAULT_PROFILE_ID: [
        {
            "key": "follow_ups",
            "title": "待我跟进",
            "item_types": ["SUGGESTED_ACTION"],
            "show_if_any": True,
        },
        {
            "key": "next_visit",
            "title": "下次沟通",
            "item_types": ["NEXT_VISIT_PLAN"],
            "show_if_any": True,
        },
        {
            "key": "key_issues",
            "title": "当前最关键问题",
            "item_types": ["KEY_GAP", "RISK"],
            "require_any": ["KEY_GAP", "RISK"],
        },
        {
            "key": "potential_opps",
            "title": "潜在新商机",
            "item_types": ["POTENTIAL_OPP"],
            "show_if_any": True,
        },
    ]
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _feedback_at_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _normalize_feedback_comment(comment: Optional[str]) -> Optional[str]:
    text = str(comment or "").strip()
    if not text:
        return None
    return text[:_FEEDBACK_COMMENT_MAX]


def _value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _parse_json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _parse_payload(value: Any) -> dict[str, Any]:
    parsed = _parse_json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _parse_card_link_specs(value: Any) -> list[dict[str, Any]]:
    parsed = _parse_json_value(value)
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("key")]


def resolve_card_link_specs(
    rows: Sequence[Mapping[str, Any]],
    profile_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    for row in rows:
        specs = _parse_card_link_specs(row.get("card_links"))
        if specs:
            return specs
    return card_links_for_profile(profile_id)


def _same_user(left: Any, right: Any) -> bool:
    def _token(value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "")

    viewer = _token(left)
    owner = _token(right)
    return bool(viewer and owner and viewer == owner)


def serialize_extract_item(
    row: Mapping[str, Any],
    *,
    viewer_user_id: Any = None,
) -> dict[str, Any]:
    payload = _parse_payload(row.get("payload"))
    reserved = {"claim", "why", "evidence", "confidence"}
    extras = {k: v for k, v in payload.items() if k not in reserved}
    owner = _text(row.get("feedback_user_id")) or None
    visible = _same_user(viewer_user_id, owner)
    data: dict[str, Any] = {
        "unique_id": _text(row.get("unique_id")),
        "item_type": _text(row.get("item_type")),
        "extract_key": _text(row.get("extract_key")),
        "claim": payload.get("claim"),
        "why": payload.get("why"),
        "evidence": payload.get("evidence") or [],
        "confidence": payload.get("confidence"),
        "severity": _text(row.get("severity")) or None,
        "status": _text(row.get("status")),
        "adopted_todo_id": _text(row.get("adopted_todo_id")) or None,
        "reject_reason": _text(row.get("reject_reason")) or None,
        "feedback": (_text(row.get("feedback")) or None) if visible else None,
        "feedback_comment": (_text(row.get("feedback_comment")) or None) if visible else None,
        "feedback_user_id": owner if visible else None,
        "feedback_at": _feedback_at_text(row.get("feedback_at")) if visible else None,
    }
    data.update(extras)
    return data


def _action_sort_key(item: dict[str, Any]) -> tuple:
    origin = str(item.get("origin") or "ai_recommendation").strip().lower()
    origin_rank = 0 if origin != "ai_recommendation" else 1
    due_rank = 0 if item.get("due_hint") else 1
    priority = str(item.get("priority") or "").upper()
    p_rank = 0 if priority == "P0" else 1
    confidence = float(item.get("confidence") or 0)
    return (origin_rank, due_rank, p_rank, -confidence)


def group_extract_items(serialized: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in serialized:
        grouped.setdefault(item["item_type"], []).append(item)
    for item_type, rows in grouped.items():
        if item_type == "SUGGESTED_ACTION":
            rows.sort(key=_action_sort_key)
        else:
            rows.sort(key=lambda row: float(row.get("confidence") or 0), reverse=True)
    return grouped


def card_links_for_profile(profile_id: Optional[str]) -> list[dict[str, Any]]:
    pid = (profile_id or "").strip() or DEFAULT_PROFILE_ID
    return CARD_LINKS_BY_PROFILE.get(pid) or CARD_LINKS_BY_PROFILE[DEFAULT_PROFILE_ID]


def build_card_links(
    grouped: dict[str, list[dict[str, Any]]],
    specs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for spec in specs:
        types = [str(t) for t in (spec.get("item_types") or [])]
        require_any = [str(t) for t in (spec.get("require_any") or [])]
        collected: list[dict[str, Any]] = []
        for item_type in types:
            collected.extend(grouped.get(item_type) or [])
        if require_any:
            if not any(grouped.get(item_type) for item_type in require_any):
                continue
        elif spec.get("show_if_any", True) and not collected:
            continue
        preview_pool = collected
        if require_any:
            preview_pool = []
            for item_type in require_any:
                preview_pool.extend(grouped.get(item_type) or [])
            if not preview_pool:
                preview_pool = collected
        preview = preview_pool[0].get("claim") if preview_pool else None
        links.append(
            {
                "key": spec.get("key"),
                "title": spec.get("title"),
                "count": len(collected),
                "preview": preview,
                "item_types": types,
            }
        )
    return links


def empty_extract_response(visit_id: str, profile_id: str = DEFAULT_PROFILE_ID) -> dict[str, Any]:
    return {
        "visit_id": visit_id,
        "profile_id": profile_id,
        "items": {},
        "card_links": [],
    }


def build_extract_response(
    *,
    visit_id: str,
    rows: Sequence[Mapping[str, Any]],
    profile_id: Optional[str] = None,
    viewer_user_id: Any = None,
) -> dict[str, Any]:
    serialized = [
        serialize_extract_item(row, viewer_user_id=viewer_user_id) for row in rows
    ]
    grouped = group_extract_items(serialized)
    pid = profile_id or DEFAULT_PROFILE_ID
    if rows:
        pid = _text(rows[0].get("profile_id")) or pid
    return {
        "visit_id": visit_id,
        "profile_id": pid,
        "items": grouped,
        "card_links": build_card_links(grouped, resolve_card_link_specs(rows, pid)),
    }


def _as_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return row.model_dump()


def _active_extract_statement(
    visit_id: str,
    *,
    viewer_role: str = VIEWER_ROLE_SALES,
    unique_ids: Optional[Sequence[str]] = None,
):
    statement = select(CRMPostvisitExtractItem).where(
        CRMPostvisitExtractItem.visit_id == visit_id,
        CRMPostvisitExtractItem.viewer_role == viewer_role,
        CRMPostvisitExtractItem.is_deleted == 0,
        CRMPostvisitExtractItem.status != EXTRACT_STATUS_SUPERSEDED,
    )
    if unique_ids is not None:
        statement = statement.where(CRMPostvisitExtractItem.unique_id.in_(unique_ids))
    return statement


def _query_extract_rows(
    session: Session,
    visit_id: str,
    viewer_role: str = VIEWER_ROLE_SALES,
) -> Optional[list[Mapping[str, Any]]]:
    try:
        rows = session.exec(
            _active_extract_statement(visit_id, viewer_role=viewer_role)
        ).all()
        return [_as_mapping(row) for row in rows]
    except Exception as exc:
        logger.warning(
            "Failed to load visit extract items, visit_id=%s: %s",
            visit_id,
            exc,
        )
        return None


def load_visit_record_extract_response(
    session: Optional[Session],
    record_id: str,
    *,
    viewer_role: str = VIEWER_ROLE_SALES,
    viewer_user_id: Any = None,
) -> dict[str, Any]:
    """读取拜访销售视角有效抽取条目，组装 items + card_links。

    赞或踩只在 feedback_user_id 是当前用户时返回。
    """
    rid = (record_id or "").strip()
    empty = empty_extract_response(rid)
    if session is None or not rid:
        return empty
    rows = _query_extract_rows(session, rid, viewer_role=viewer_role)
    if not rows:
        return empty
    return build_extract_response(visit_id=rid, rows=rows, viewer_user_id=viewer_user_id)


def _log_extract_interaction(
    *,
    visit_id: str,
    unique_id: str,
    user_id: str,
    kind: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> None:
    logger.info(
        "visit_extract_interaction visit_id=%s unique_id=%s user_id=%s kind=%s before=%s after=%s",
        visit_id,
        unique_id,
        user_id,
        kind,
        json.dumps(before, ensure_ascii=False, default=str),
        json.dumps(after, ensure_ascii=False, default=str),
    )


def _feedback_snapshot(row: Any) -> dict[str, Any]:
    if row is None:
        return {
            "feedback": None,
            "feedback_comment": None,
            "feedback_user_id": None,
            "feedback_at": None,
        }
    return {
        "feedback": _text(_value(row, "feedback")) or None,
        "feedback_comment": _text(_value(row, "feedback_comment")) or None,
        "feedback_user_id": _text(_value(row, "feedback_user_id")) or None,
        "feedback_at": _feedback_at_text(_value(row, "feedback_at")),
    }


def _decision_snapshot(row: Any) -> dict[str, Any]:
    if row is None:
        return {"status": None, "adopted_todo_id": None, "reject_reason": None}
    return {
        "status": _text(_value(row, "status")) or None,
        "adopted_todo_id": _text(_value(row, "adopted_todo_id")) or None,
        "reject_reason": _text(_value(row, "reject_reason")) or None,
    }


def save_visit_record_extract_feedback(
    session: Session,
    *,
    visit_id: str,
    unique_id: str,
    feedback: Optional[str],
    feedback_comment: Optional[str],
    user_id: str,
) -> Optional[dict[str, Any]]:
    """更新一条有效抽取的点赞点踩。找不到行时返回 None。"""
    rid = (visit_id or "").strip()
    item_id = (unique_id or "").strip()
    voter = (user_id or "").strip()
    if not rid or not item_id or not voter:
        return None
    if feedback not in (None, FEEDBACK_UP, FEEDBACK_DOWN):
        raise ValueError(f"unsupported feedback: {feedback}")

    vote = feedback
    note = _normalize_feedback_comment(feedback_comment) if vote else None
    voted_at = datetime.now(timezone.utc).replace(tzinfo=None) if vote else None
    voted_by = voter if vote else None

    current = session.exec(
        _active_extract_statement(rid, unique_ids=[item_id])
    ).first()
    if current is None:
        return None

    before = _feedback_snapshot(current)
    current.feedback = vote
    current.feedback_comment = note
    current.feedback_user_id = voted_by
    current.feedback_at = voted_at
    session.add(current)
    after = {
        "feedback": vote,
        "feedback_comment": note,
        "feedback_user_id": voted_by,
        "feedback_at": voted_at.isoformat() if voted_at else None,
    }
    session.commit()
    _log_extract_interaction(
        visit_id=rid,
        unique_id=item_id,
        user_id=voter,
        kind="feedback",
        before=before,
        after=after,
    )
    return {"unique_id": item_id, "visit_id": rid, **after}


def _normalize_decision_item(item: Mapping[str, Any]) -> dict[str, Any]:
    action = _text(item.get("action"))
    if action not in (DECISION_ADOPT, DECISION_REJECT):
        raise ValueError(f"unsupported decision: {action}")
    adopted_todo_id = _text(item.get("adopted_todo_id")) or None
    reject_reason = _text(item.get("reject_reason")) or None
    if reject_reason:
        reject_reason = reject_reason[:_REJECT_REASON_MAX]
    if action == DECISION_ADOPT:
        return {
            "unique_id": _text(item.get("unique_id")),
            "action": action,
            "status": EXTRACT_STATUS_ADOPTED,
            "adopted_todo_id": adopted_todo_id,
            "reject_reason": None,
        }
    return {
        "unique_id": _text(item.get("unique_id")),
        "action": action,
        "status": EXTRACT_STATUS_REJECTED,
        "adopted_todo_id": None,
        "reject_reason": reject_reason,
    }


def save_visit_record_extract_decisions(
    session: Session,
    *,
    visit_id: str,
    user_id: str,
    items: Sequence[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    """批量采纳或拒绝。行上只留最新决定，每条变更写入日志。

    找不到的条目记为未成功，其余照常提交。
    """
    rid = (visit_id or "").strip()
    actor = (user_id or "").strip()
    if not rid or not actor:
        return None
    if not items or len(items) > _DECISION_BATCH_MAX:
        raise ValueError("decision batch size must be 1..100")

    normalized = [_normalize_decision_item(item) for item in items]
    ids = [item["unique_id"] for item in normalized]
    if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("unique_id must be present and unique")

    found_rows = session.exec(
        _active_extract_statement(rid, unique_ids=ids)
    ).all()
    found = {_text(_value(row, "unique_id")): row for row in found_rows}

    results: list[dict[str, Any]] = []
    applied: list[tuple[dict[str, Any], str, dict[str, Any], str]] = []
    for item in normalized:
        current = found.get(item["unique_id"])
        if current is None:
            results.append({"unique_id": item["unique_id"], "ok": False})
            continue
        before = _decision_snapshot(current)
        after = {
            "status": item["status"],
            "adopted_todo_id": item["adopted_todo_id"],
            "reject_reason": item["reject_reason"],
        }
        current.status = after["status"]
        current.adopted_todo_id = after["adopted_todo_id"]
        current.reject_reason = after["reject_reason"]
        session.add(current)
        applied.append((before, item["action"], after, item["unique_id"]))
        results.append({"unique_id": item["unique_id"], "ok": True, **after})

    if applied:
        session.commit()
        for before, kind, after, item_id in applied:
            _log_extract_interaction(
                visit_id=rid,
                unique_id=item_id,
                user_id=actor,
                kind=kind,
                before=before,
                after=after,
            )
    return {"visit_id": rid, "results": results}

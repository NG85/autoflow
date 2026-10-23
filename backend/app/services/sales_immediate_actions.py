"""一线销售处理自己的结构化抽取：赞踩和采纳写在抽取行上。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.services.leader_intervention_actions import (
    STATUS_ADOPTED,
    interaction_fields,
    require_active_todo,
)
from app.services.sales_today_highlights import (
    AUDIENCE_SALES,
    STATUS_SUPERSEDED,
    SUBJECT_USER,
    _text,
)

logger = logging.getLogger(__name__)

STATUS_REJECTED = "REJECTED"
DECISION_ADOPT = "adopt"
DECISION_REJECT = "reject"
_DECISION_BATCH_MAX = 100
_REJECT_REASON_MAX = 1000


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _decision_patch(item: Mapping[str, Any]) -> dict[str, Any]:
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
            "status": STATUS_ADOPTED,
            "adopted_todo_id": adopted_todo_id,
            "reject_reason": None,
        }
    return {
        "unique_id": _text(item.get("unique_id")),
        "status": STATUS_REJECTED,
        "adopted_todo_id": None,
        "reject_reason": reject_reason,
    }


def _is_own_extract(row: Any, oauth_user_id: str) -> bool:
    owner = _text(oauth_user_id)
    if not owner or row is None:
        return False
    if int(getattr(row, "is_deleted", 0) or 0) != 0:
        return False
    if _text(getattr(row, "status", None)).upper() == STATUS_SUPERSEDED:
        return False
    if _text(getattr(row, "subject_type", None)).upper() != SUBJECT_USER:
        return False
    if _text(getattr(row, "audience", None)).lower() != AUDIENCE_SALES:
        return False
    return _text(getattr(row, "subject_id", None)) == owner


def load_own_highlight_item(
    session: Session,
    oauth_user_id: str,
    unique_id: str,
) -> Optional[CRMSkillExtractItem]:
    """当前用户自己的、未删除且未作废的销售抽取。否则 None。"""
    item_id = _text(unique_id)
    owner = _text(oauth_user_id)
    if not item_id or not owner:
        return None
    row = session.exec(
        select(CRMSkillExtractItem).where(CRMSkillExtractItem.unique_id == item_id)
    ).first()
    if not _is_own_extract(row, owner):
        return None
    return row


def save_sales_highlight_decisions(
    session: Session,
    oauth_user_id: str,
    user_id: Any,
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """批量采纳或拒绝自己的销售抽取。不是自己的条目 ok 为 false。"""
    if not items or len(items) > _DECISION_BATCH_MAX:
        raise ValueError("decision batch size must be 1..100")
    normalized = [_decision_patch(item) for item in items]
    ids = [item["unique_id"] for item in normalized]
    if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("unique_id must be present and unique")
    for item in normalized:
        if item["status"] == STATUS_ADOPTED and item["adopted_todo_id"]:
            require_active_todo(session, item["adopted_todo_id"])
    found_rows = session.exec(
        select(CRMSkillExtractItem).where(CRMSkillExtractItem.unique_id.in_(ids))
    ).all()
    if not isinstance(found_rows, list):
        found_rows = []
    found = {_text(getattr(row, "unique_id", None)): row for row in found_rows}
    results: list[dict[str, Any]] = []
    applied = False
    for item in normalized:
        current = found.get(item["unique_id"])
        if current is None or not _is_own_extract(current, oauth_user_id):
            results.append({"unique_id": item["unique_id"], "ok": False})
            continue
        before = interaction_fields(current)
        current.status = item["status"]
        adopted_todo_id = item["adopted_todo_id"]
        if item["status"] == STATUS_ADOPTED and not adopted_todo_id:
            adopted_todo_id = _text(getattr(current, "adopted_todo_id", None)) or None
        current.adopted_todo_id = adopted_todo_id
        current.reject_reason = item["reject_reason"]
        current.updated_at = _now()
        session.add(current)
        applied = True
        after = interaction_fields(current)
        logger.info(
            "销售抽取决定 unique_id=%s user_id=%s before=%s after=%s",
            item["unique_id"],
            user_id,
            before,
            after,
        )
        results.append({"unique_id": item["unique_id"], "ok": True, **after})
    if applied:
        session.commit()
    return {"results": results}

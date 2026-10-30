"""负责人处理今日需介入：赞踩和采纳写抽取行，评论和任务外挂。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.services.department_today_highlights import (
    ITEM_INTERVENTION,
    STATUS_SUPERSEDED,
    SUBJECT_USER,
    _payload,
    _text,
    leader_oauth_id,
    load_display_names,
)
from app.services.subject_interaction import (
    SCENE_DEPARTMENT_TODAY_HIGHLIGHTS,
    SUBJECT_DEPARTMENT_INTERVENTION,
    append_comments,
    append_todo_id,
    intervention_anchor,
    load_task_summaries,
    load_threads,
)
from app.utils.crm_comments import (
    comment_message_tail,
    comment_note_lines,
    comment_subject_label,
    resolve_comment_notify_recipients,
)

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_ADOPTED = "ADOPTED"
STATUS_REJECTED = "REJECTED"
FEEDBACK_UP = "up"
FEEDBACK_DOWN = "down"
DECISION_ADOPT = "adopt"
DECISION_REJECT = "reject"
_REJECT_REASON_MAX = 1000
_DECISION_BATCH_MAX = 100


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _dt_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text_value = _text(value)
    return text_value or None


def interaction_fields(row: Any) -> dict[str, Any]:
    """抽取行上的采纳和赞踩。没有决定时状态为 PENDING。"""
    status = _text(getattr(row, "status", None) if not isinstance(row, Mapping) else row.get("status")).upper()
    if status not in (STATUS_ADOPTED, STATUS_REJECTED, STATUS_PENDING):
        status = STATUS_PENDING
    feedback = _text(
        getattr(row, "feedback", None) if not isinstance(row, Mapping) else row.get("feedback")
    ).lower()
    return {
        "status": status,
        "reject_reason": _text(
            getattr(row, "reject_reason", None) if not isinstance(row, Mapping) else row.get("reject_reason")
        )
        or None,
        "adopted_todo_id": _text(
            getattr(row, "adopted_todo_id", None)
            if not isinstance(row, Mapping)
            else row.get("adopted_todo_id")
        )
        or None,
        "feedback": feedback if feedback in (FEEDBACK_UP, FEEDBACK_DOWN) else None,
        "feedback_comment": _text(
            getattr(row, "feedback_comment", None)
            if not isinstance(row, Mapping)
            else row.get("feedback_comment")
        )
        or None,
        "feedback_at": _dt_text(
            getattr(row, "feedback_at", None) if not isinstance(row, Mapping) else row.get("feedback_at")
        ),
    }


def _item_anchor(report_date: Any, department_id: Any, item: Mapping[str, Any]) -> str:
    return intervention_anchor(
        report_date,
        department_id,
        item.get("extract_key"),
        item.get("unique_id"),
    )


def attach_item_interactions(
    session: Session,
    departments: Sequence[Mapping[str, Any]],
    report_date: Any,
) -> None:
    """把评论线程和任务摘要拼进今日需介入条目。按当天稳定键查找。"""
    items: list[tuple[str, dict[str, Any]]] = []
    for department in departments or []:
        grouped = department.get("items") if isinstance(department, Mapping) else None
        if not isinstance(grouped, Mapping):
            continue
        department_id = department.get("subject_id") or department.get("department_id")
        for item in grouped.get("leader_intervene") or []:
            if isinstance(item, dict):
                items.append((_item_anchor(report_date, department_id, item), item))
    ids = [anchor for anchor, _item in items if anchor]
    threads = load_threads(session, SUBJECT_DEPARTMENT_INTERVENTION, ids)
    todo_ids: list[str] = []
    for thread in threads.values():
        todo_ids.extend(thread.get("todo_ids") or [])
    summaries = load_task_summaries(session, todo_ids)
    for anchor, item in items:
        thread = threads.get(anchor) or {}
        item["comments"] = list(thread.get("comments") or [])
        item["tasks"] = [
            summaries.get(task_id)
            or {
                "unique_id": task_id,
                "title": None,
                "owner_id": None,
                "owner_name": None,
                "due_date": None,
                "status": None,
                "ai_status": None,
            }
            for task_id in thread.get("todo_ids") or []
        ]


def load_actionable_item(
    session: Session,
    user_id: Any,
    unique_id: str,
) -> Optional[CRMSkillExtractItem]:
    """当前负责人自己的、未删除且未作废的今日需介入。否则 None。"""
    item_id = _text(unique_id)
    oauth_id = leader_oauth_id(session, user_id)
    if not item_id or not oauth_id:
        return None
    row = session.exec(
        select(CRMSkillExtractItem).where(CRMSkillExtractItem.unique_id == item_id)
    ).first()
    if not _is_actionable(row, oauth_id):
        return None
    return row


def save_feedback(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    user_id: str,
    feedback: Optional[str],
    feedback_comment: Optional[str],
) -> dict[str, Any]:
    """更新一条介入的赞踩。feedback 为空时撤销。"""
    if feedback not in (None, FEEDBACK_UP, FEEDBACK_DOWN):
        raise ValueError(f"unsupported feedback: {feedback}")
    vote = feedback
    note = (_text(feedback_comment)[:500] or None) if vote else None
    voted_at = _now() if vote else None
    voted_by = _text(user_id) if vote else None
    before = interaction_fields(row)
    row.feedback = vote
    row.feedback_comment = note
    row.feedback_user_id = voted_by
    row.feedback_at = voted_at
    row.updated_at = _now()
    session.add(row)
    session.commit()
    after = interaction_fields(row)
    logger.info(
        "介入赞踩 unique_id=%s user_id=%s before=%s after=%s",
        row.unique_id,
        user_id,
        before,
        after,
    )
    return {
        "unique_id": row.unique_id,
        "feedback": after["feedback"],
        "feedback_comment": after["feedback_comment"],
        "feedback_at": after["feedback_at"],
    }


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


def save_decisions(
    session: Session,
    user_id: Any,
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """批量采纳或拒绝。无权限或不存在的条目 ok 为 false。"""
    if not items or len(items) > _DECISION_BATCH_MAX:
        raise ValueError("decision batch size must be 1..100")
    normalized = [_decision_patch(item) for item in items]
    ids = [item["unique_id"] for item in normalized]
    if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("unique_id must be present and unique")
    for item in normalized:
        if item["status"] == STATUS_ADOPTED and item["adopted_todo_id"]:
            require_active_todo(session, item["adopted_todo_id"])
    oauth_id = leader_oauth_id(session, user_id)
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
        if current is None or not _is_actionable(current, oauth_id):
            results.append({"unique_id": item["unique_id"], "ok": False})
            continue
        before = interaction_fields(current)
        current.status = item["status"]
        current.adopted_todo_id = item["adopted_todo_id"]
        current.reject_reason = item["reject_reason"]
        current.updated_at = _now()
        session.add(current)
        applied = True
        after = interaction_fields(current)
        logger.info(
            "介入决定 unique_id=%s user_id=%s before=%s after=%s",
            item["unique_id"],
            user_id,
            before,
            after,
        )
        results.append({"unique_id": item["unique_id"], "ok": True, **after})
    if applied:
        session.commit()
    return {"results": results}


def _is_actionable(row: Optional[CRMSkillExtractItem], oauth_id: str) -> bool:
    owner = _text(oauth_id)
    if row is None or not owner:
        return False
    if int(getattr(row, "is_deleted", 0) or 0) != 0:
        return False
    if _text(getattr(row, "status", None)).upper() == STATUS_SUPERSEDED:
        return False
    if _text(getattr(row, "subject_type", None)).upper() != SUBJECT_USER:
        return False
    if _text(getattr(row, "audience", None)).lower() != "leader":
        return False
    if _text(getattr(row, "item_type", None)) != ITEM_INTERVENTION:
        return False
    return _text(getattr(row, "subject_id", None)) == owner


def save_item_comments(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    user_id: str,
    author: str,
    comments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return append_comments(
        session,
        subject_type=SUBJECT_DEPARTMENT_INTERVENTION,
        subject_id=_row_anchor(row),
        scene=SCENE_DEPARTMENT_TODAY_HIGHLIGHTS,
        actor_user_id=user_id,
        author=author,
        comments=comments,
    )


def _comment_preview(content: Any) -> str:
    preview = _text(content)
    if len(preview) > 200:
        return preview[:197] + "..."
    return preview


def _highlight_comment_context(session: Session, row: CRMSkillExtractItem) -> str:
    payload = _payload(row)
    names = load_display_names(session, [row])
    account_id = _text(getattr(row, "account_id", None) or payload.get("account_id"))
    opportunity_id = _text(getattr(row, "opportunity_id", None) or payload.get("opportunity_id"))
    account = _text((names.get("accounts") or {}).get(account_id))
    opportunity = _text((names.get("opportunities") or {}).get(opportunity_id))
    return comment_subject_label(account, opportunity) or comment_subject_label(payload.get("claim"))


def notify_item_comments(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    current_user_id: str,
    author: str,
    comments: Sequence[Mapping[str, Any]],
    saved_comments: Sequence[Mapping[str, Any]],
    recipient_user_ids: Optional[Sequence[Any]] = None,
) -> None:
    """保存成功后推送今日需介入评论提醒。失败不影响已保存的评论。"""
    try:
        from app.services.platform_notification_service import platform_notification_service

        comment_by_id: dict[str, Mapping[str, Any]] = {}
        for raw in saved_comments or []:
            if not isinstance(raw, Mapping):
                continue
            cid = _text(raw.get("id"))
            if cid:
                comment_by_id[cid] = raw

        author_name = _text(author) or "有人"
        context = _highlight_comment_context(session, row)
        matter = _text(_payload(row).get("claim"))
        current_user_id_str = _text(current_user_id)

        for item in comments or []:
            if not isinstance(item, Mapping):
                continue
            preview = _comment_preview(item.get("content")) or "--"
            reply_to_id = _text(item.get("reply_to_id"))
            parent_content = ""
            if reply_to_id:
                parent = comment_by_id.get(reply_to_id)
                default_recipient = _text((parent or {}).get("author_id"))
                parent_content = _text((parent or {}).get("content"))
                label = "回复"
            else:
                default_recipient = ""
                label = "评论"
            tail = comment_message_tail(
                content=preview,
                parent_content=parent_content,
                include_parent=bool(reply_to_id),
                label=label,
            )
            recipients = resolve_comment_notify_recipients(
                specified=recipient_user_ids,
                default_user_id=default_recipient,
                current_user_id=current_user_id_str,
            )
            if not recipients:
                logger.info(
                    "today_highlight_comment notify skipped: unique_id=%s, reply_to_id=%s, reason=invalid_recipient_or_self_notification",
                    getattr(row, "unique_id", None),
                    reply_to_id or None,
                )
                continue
            for recipient_user_id in recipients:
                header, sentence, matter_line = comment_note_lines(
                    author_name=author_name,
                    entity_name=context,
                    matter=matter,
                )
                lines = [header, sentence]
                if matter_line:
                    lines.append(matter_line)
                lines.extend(tail)
                text = "\n".join(lines) + "\n"
                platform_notification_service.send_today_highlight_comment_notification(
                    session,
                    recipient_user_id=recipient_user_id,
                    message_text=text,
                )
                logger.info(
                    "today_highlight_comment notify sent: unique_id=%s, kind=%s, recipient_user_id=%s, author_name=%s",
                    getattr(row, "unique_id", None),
                    "reply" if reply_to_id else "comment",
                    recipient_user_id,
                    author_name,
                )
    except Exception as exc:
        logger.warning("发送今日需介入评论提醒失败（不影响保存评论）：%s", exc)


def _row_anchor(row: CRMSkillExtractItem) -> str:
    return intervention_anchor(
        getattr(row, "report_date", None),
        getattr(row, "subject_id", None),
        getattr(row, "extract_key", None),
        getattr(row, "unique_id", None),
    )


def require_active_todo(session: Session, todo_id: str) -> str:
    """确认任务未删除。不存在时抛出 ValueError，调用方不要改采纳状态。"""
    task_id = _text(todo_id)
    if not task_id:
        raise ValueError("todo_id is required")
    try:
        found = session.exec(
            text(
                """
                SELECT unique_id
                FROM crm_todos
                WHERE unique_id = :todo_id
                  AND is_deleted = 0
                """
            ),
            params={"todo_id": task_id},
        ).all()
    except Exception as exc:
        logger.warning("核对任务失败 todo_id=%s: %s", task_id, exc)
        raise ValueError("任务不存在或已删除") from exc
    if not isinstance(found, list) or not found:
        raise ValueError("任务不存在或已删除")
    return task_id


def link_item_task(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    user_id: str,
    todo_id: str,
    adopt: bool,
) -> dict[str, Any]:
    """把其他服务已建好的任务编号挂到这条介入上。任务不存在则不改采纳状态。"""
    task_id = require_active_todo(session, todo_id)
    append_todo_id(
        session,
        subject_type=SUBJECT_DEPARTMENT_INTERVENTION,
        subject_id=_row_anchor(row),
        scene=SCENE_DEPARTMENT_TODAY_HIGHLIGHTS,
        actor_user_id=_text(user_id),
        todo_id=task_id,
    )
    if adopt:
        row.status = STATUS_ADOPTED
        row.adopted_todo_id = task_id
        row.reject_reason = None
        row.updated_at = _now()
        session.add(row)
    session.commit()
    logger.info(
        "介入关联任务 unique_id=%s todo_id=%s user_id=%s adopt=%s",
        row.unique_id,
        task_id,
        user_id,
        adopt,
    )
    return {
        "unique_id": _text(row.unique_id),
        "todo_id": task_id,
        "adopted": adopt,
        "status": interaction_fields(row)["status"],
    }

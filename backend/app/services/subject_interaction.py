"""按业务对象挂载评论和任务编号。

同一张表，一行一条。kind=comment 是评论，kind=task 是写回的任务。
评论用 reply_to_id 盖楼，接口仍返回扁平列表。任务正文在 crm_todos。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.crm_subject_interaction import CRMSubjectInteraction

logger = logging.getLogger(__name__)

SUBJECT_SKILL_EXTRACT_ITEM = "skill_extract_item"
SUBJECT_DEPARTMENT_INTERVENTION = "department_intervention"
SUBJECT_LEADER_MEETING_REVIEW = "leader_meeting_review"
SCENE_DEPARTMENT_TODAY_HIGHLIGHTS = "department_today_highlights"
SCENE_LEADER_MEETING_REVIEW = "leader_meeting_review"
_ANCHOR_MAX = 255
KIND_COMMENT = "comment"
KIND_TASK = "task"


class SubjectInteractionError(ValueError):
    """评论或任务关联写不进去。"""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def intervention_anchor(
    report_date: Any,
    department_id: Any,
    extract_key: Any,
    fallback_id: Any = "",
) -> str:
    """当天不变的挂载键：业务日、部门、抽取键。重抽换 unique_id 时评论和任务还在。"""
    day = report_date.isoformat() if hasattr(report_date, "isoformat") else _text(report_date)
    key = _text(extract_key) or _text(fallback_id)
    raw = f"{day}|{_text(department_id)}|{key}"
    if len(raw) <= _ANCHOR_MAX:
        return raw
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


def _rows_or_empty(result: Any) -> list:
    rows = result.all() if result is not None else []
    return rows if isinstance(rows, list) else []


def _comment_dict(row: CRMSubjectInteraction) -> dict[str, Any]:
    created_at = getattr(row, "created_at", None)
    created_text = created_at.isoformat() if hasattr(created_at, "isoformat") else (_text(created_at) or None)
    reply_to_id = _text(getattr(row, "reply_to_id", None)) or None
    return {
        "id": _text(getattr(row, "id", None)),
        "reply_to_id": reply_to_id,
        "author_id": _text(getattr(row, "author_id", None)),
        "author": _text(getattr(row, "author", None)),
        "content": _text(getattr(row, "content", None)),
        "created_at": created_text,
    }


def _rows_for_subjects(
    session: Session,
    subject_type: str,
    subject_ids: Sequence[str],
) -> list[CRMSubjectInteraction]:
    return _rows_or_empty(
        session.exec(
            select(CRMSubjectInteraction)
            .where(
                CRMSubjectInteraction.subject_type == subject_type,
                CRMSubjectInteraction.subject_id.in_(list(subject_ids)),
            )
            .order_by(CRMSubjectInteraction.created_at, CRMSubjectInteraction.id)
        )
    )


def _rows_for_subject(session: Session, subject_type: str, subject_id: str) -> list[CRMSubjectInteraction]:
    return _rows_for_subjects(session, subject_type, [subject_id])


def _is_kind(row: CRMSubjectInteraction, kind: str) -> bool:
    return _text(getattr(row, "kind", None)) == kind


def load_threads(
    session: Session,
    subject_type: str,
    subject_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """按对象取评论和任务编号。查询失败时全部视为没有。"""
    ids = list(dict.fromkeys(_text(item) for item in subject_ids if _text(item)))
    if not ids:
        return {}
    found: dict[str, dict[str, Any]] = {}
    try:
        rows = _rows_for_subjects(session, subject_type, ids)
    except Exception as exc:
        logger.warning("读取对象互动失败: %s", exc)
        return {}
    for row in rows:
        subject_id = _text(getattr(row, "subject_id", None))
        if not subject_id:
            continue
        bucket = found.setdefault(subject_id, {"comments": [], "todo_ids": []})
        if _is_kind(row, KIND_COMMENT):
            bucket["comments"].append(_comment_dict(row))
        elif _is_kind(row, KIND_TASK):
            task_id = _text(getattr(row, "todo_id", None))
            if task_id and task_id not in bucket["todo_ids"]:
                bucket["todo_ids"].append(task_id)
    return found


def load_task_summaries(session: Session, todo_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """任务摘要。表不可用时只返回编号。"""
    ids = list(dict.fromkeys(_text(item) for item in todo_ids if _text(item)))
    if not ids:
        return {}
    summaries = {
        item: {
            "unique_id": item,
            "title": None,
            "owner_id": None,
            "owner_name": None,
            "due_date": None,
            "status": None,
            "ai_status": None,
        }
        for item in ids
    }
    placeholders = ", ".join(f":todo_{index}" for index in range(len(ids)))
    params = {f"todo_{index}": item for index, item in enumerate(ids)}
    try:
        rows = session.exec(
            text(
                f"""
                SELECT unique_id, title, owner_id, owner_name, due_date, status, ai_status
                FROM crm_todos
                WHERE unique_id IN ({placeholders})
                  AND is_deleted = 0
                """
            ),
            params=params,
        ).all()
    except Exception as exc:
        logger.warning("读取介入任务摘要失败: %s", exc)
        return summaries
    if not isinstance(rows, list):
        return summaries
    for row in rows:
        if isinstance(row, Mapping):
            unique_id = _text(row.get("unique_id"))
            title = row.get("title")
            owner_id = row.get("owner_id")
            owner_name = row.get("owner_name")
            due_date = row.get("due_date")
            status = row.get("status")
            ai_status = row.get("ai_status")
        else:
            unique_id = _text(row[0] if len(row) > 0 else None)
            title = row[1] if len(row) > 1 else None
            owner_id = row[2] if len(row) > 2 else None
            owner_name = row[3] if len(row) > 3 else None
            due_date = row[4] if len(row) > 4 else None
            status = row[5] if len(row) > 5 else None
            ai_status = row[6] if len(row) > 6 else None
        if not unique_id or unique_id not in summaries:
            continue
        due_text = due_date.isoformat() if hasattr(due_date, "isoformat") else (_text(due_date) or None)
        status_text = _text(status) or _text(ai_status) or None
        summaries[unique_id] = {
            "unique_id": unique_id,
            "title": _text(title) or None,
            "owner_id": _text(owner_id) or None,
            "owner_name": _text(owner_name) or None,
            "due_date": due_text,
            "status": status_text,
            "ai_status": _text(ai_status) or None,
        }
    return summaries


def append_comments(
    session: Session,
    *,
    subject_type: str,
    subject_id: str,
    scene: str,
    actor_user_id: str,
    author: str,
    comments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """追加评论。reply_to_id 必须指向本对象已有评论。返回追加后的整段线程。"""
    actor = _text(actor_user_id)
    if not actor:
        raise SubjectInteractionError("缺少当前用户")
    incoming = list(comments or [])
    if not incoming:
        raise SubjectInteractionError("评论不能为空")
    existing = [row for row in _rows_for_subject(session, subject_type, subject_id) if _is_kind(row, KIND_COMMENT)]
    known = {_text(getattr(row, "id", None)) for row in existing if _text(getattr(row, "id", None))}
    pending: list[CRMSubjectInteraction] = []
    base = _now()
    for index, item in enumerate(incoming):
        if not isinstance(item, Mapping):
            raise SubjectInteractionError("评论格式不正确")
        content = _text(item.get("content"))
        if not content:
            raise SubjectInteractionError("评论内容不能为空")
        reply_to_id = _text(item.get("reply_to_id")) or None
        if reply_to_id and reply_to_id not in known:
            raise SubjectInteractionError(f"reply_to_id 无效：未找到 id={reply_to_id} 的评论")
        comment = CRMSubjectInteraction(
            id=str(uuid4()),
            subject_type=subject_type,
            subject_id=subject_id,
            kind=KIND_COMMENT,
            scene=_text(scene),
            reply_to_id=reply_to_id,
            author_id=actor,
            author=_text(item.get("author")) or _text(author),
            content=content[:2000],
            created_at=base + timedelta(microseconds=index),
        )
        pending.append(comment)
        known.add(comment.id)
    for comment in pending:
        session.add(comment)
    session.commit()
    return [_comment_dict(row) for row in [*existing, *pending]]


def save_subject_comments(
    session: Session,
    *,
    subject_type: str,
    subject_id: str,
    scene: str,
    actor_user_id: str,
    author: str,
    comments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """追加、回复或修改评论。带 id 时只改本人那条的正文。返回保存后的整段线程。"""
    actor = _text(actor_user_id)
    if not actor:
        raise SubjectInteractionError("缺少当前用户")
    incoming = list(comments or [])
    if not incoming:
        raise SubjectInteractionError("评论不能为空")
    existing = [row for row in _rows_for_subject(session, subject_type, subject_id) if _is_kind(row, KIND_COMMENT)]
    by_id = {_text(getattr(row, "id", None)): row for row in existing if _text(getattr(row, "id", None))}
    known = set(by_id)
    pending: list[CRMSubjectInteraction] = []
    base = _now()
    for index, item in enumerate(incoming):
        if not isinstance(item, Mapping):
            raise SubjectInteractionError("评论格式不正确")
        content = _text(item.get("content"))
        if not content:
            raise SubjectInteractionError("评论内容不能为空")
        comment_id = _text(item.get("id"))
        if comment_id:
            current = by_id.get(comment_id)
            if current is None:
                raise SubjectInteractionError(f"评论不存在：id={comment_id}")
            if _text(getattr(current, "author_id", None)) != actor:
                raise SubjectInteractionError("只能编辑本人的评论")
            current.content = content[:2000]
            session.add(current)
            continue
        reply_to_id = _text(item.get("reply_to_id")) or None
        if reply_to_id and reply_to_id not in known:
            raise SubjectInteractionError(f"reply_to_id 无效：未找到 id={reply_to_id} 的评论")
        comment = CRMSubjectInteraction(
            id=str(uuid4()),
            subject_type=subject_type,
            subject_id=subject_id,
            kind=KIND_COMMENT,
            scene=_text(scene),
            reply_to_id=reply_to_id,
            author_id=actor,
            author=_text(item.get("author")) or _text(author),
            content=content[:2000],
            created_at=base + timedelta(microseconds=index),
        )
        pending.append(comment)
        known.add(comment.id)
        by_id[comment.id] = comment
    for comment in pending:
        session.add(comment)
    session.commit()
    saved = [row for row in _rows_for_subject(session, subject_type, subject_id) if _is_kind(row, KIND_COMMENT)]
    saved_ids = {_text(getattr(row, "id", None)) for row in saved}
    for comment in pending:
        if comment.id not in saved_ids:
            saved.append(comment)
    if not saved:
        saved = [*existing, *pending]
    return [_comment_dict(row) for row in saved]


def append_todo_id(
    session: Session,
    *,
    subject_type: str,
    subject_id: str,
    scene: str,
    todo_id: str,
    actor_user_id: str = "",
) -> list[str]:
    """把已有任务编号追加到对象上。返回追加后的列表。"""
    task_id = _text(todo_id)
    if not task_id:
        raise SubjectInteractionError("缺少任务编号")
    existing = [row for row in _rows_for_subject(session, subject_type, subject_id) if _is_kind(row, KIND_TASK)]
    current = [_text(getattr(row, "todo_id", None)) for row in existing if _text(getattr(row, "todo_id", None))]
    if task_id not in current:
        session.add(
            CRMSubjectInteraction(
                id=str(uuid4()),
                subject_type=subject_type,
                subject_id=subject_id,
                kind=KIND_TASK,
                scene=_text(scene),
                author_id=_text(actor_user_id),
                todo_id=task_id,
                created_at=_now(),
            )
        )
        current.append(task_id)
    return current

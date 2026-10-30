"""例会复盘抽取的赞踩、采纳和任务。经营事项只赞踩，员工待办还可以采纳和挂任务。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from sqlmodel import Session, select

from app.models.crm_skill_extract_items import CRMSkillExtractItem
from app.services.leader_intervention_actions import (
    STATUS_ADOPTED,
    _decision_patch,
    _now,
    interaction_fields,
    require_active_todo,
    save_feedback,
)
from app.services.subject_interaction import (
    SCENE_LEADER_MEETING_REVIEW,
    SUBJECT_LEADER_MEETING_REVIEW,
    append_todo_id,
    intervention_anchor,
    load_task_summaries,
    load_threads,
    save_subject_comments,
)

logger = logging.getLogger(__name__)

ITEM_EVENT = "MANAGEMENT_EVENT"
ITEM_TODO = "EMPLOYEE_TODO_CANDIDATE"
_DECISION_BATCH_MAX = 100


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def meeting_review_anchor(row: Any) -> str:
    """重抽换 unique_id 时，同一抽取键上的任务还在。"""
    return intervention_anchor(
        getattr(row, "report_date", None),
        getattr(row, "subject_id", None),
        getattr(row, "extract_key", None),
        getattr(row, "unique_id", None),
    )


# 例会一般是本部门的，最多再上一级。再往上不插手这类管理细节。
_REPORTING_CHAIN_LEVELS = 2
# 公司级管理人员：经营层、业务管理员、系统管理员。可以看，不能改。
# 例会复盘还没有 data-scope 实体和策略，先按角色认，不查 global。
_COMPANY_MANAGER_ROLES = frozenset({"biz_admin", "executive", "sys_admin"})


def _identity_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            tokens.update(_identity_tokens(*value))
            continue
        text = _text(value).lower()
        if not text:
            continue
        tokens.add(text)
        compact = text.replace("-", "")
        if compact:
            tokens.add(compact)
    return tokens


def reporting_leader_tokens(session: Session, uploader_oauth_id: str) -> set[str]:
    """上传人汇报链上上级的 open_id。查不到或失败时为空，不因此放行。"""
    from app.repositories.user_profile import user_profile_repo
    from app.services.oauth_service import oauth_client

    profile = user_profile_repo.get_by_oauth_user_id(session, uploader_oauth_id)
    user_id = _text(getattr(profile, "user_id", None)) if profile is not None else ""
    if not user_id:
        return set()
    try:
        leaders = oauth_client.get_reporting_chain_leaders(
            base_user_id=user_id,
            max_levels=_REPORTING_CHAIN_LEVELS,
        )
    except Exception as exc:
        logger.warning("例会复盘查询汇报链失败 owner=%s: %s", uploader_oauth_id, exc)
        return set()
    return _identity_tokens(*(leader.get("open_id") for leader in leaders or []))


def viewer_is_company_manager(user_id: Any) -> bool:
    """公司级管理人员。角色查询失败时不放开。"""
    from uuid import UUID

    from app.services.oauth_service import oauth_client

    if isinstance(user_id, UUID):
        uid = user_id
    else:
        try:
            uid = UUID(str(user_id)) if user_id else None
        except (TypeError, ValueError):
            return False
    if uid is None:
        return False
    try:
        found = oauth_client.query_user_roles_and_permissions(user_id=uid)
    except Exception as exc:
        logger.warning("例会复盘查询公司级角色失败 user_id=%s: %s", uid, exc)
        return False
    roles = found.get("roles") if isinstance(found, Mapping) else None
    codes = {
        _text(role.get("code")).lower()
        for role in roles or []
        if isinstance(role, Mapping) and _text(role.get("code"))
    }
    return bool(codes & _COMPANY_MANAGER_ROLES)


def can_manage_uploader(
    session: Session,
    viewer_oauth_id: str,
    uploader_oauth_id: str,
    viewer_open_ids: Sequence[str] = (),
    cache: Optional[dict[str, set[str]]] = None,
) -> bool:
    """上传人本人，或其汇报链上的上级。"""
    owner = _text(uploader_oauth_id)
    viewer = _text(viewer_oauth_id)
    if not owner or not viewer:
        return False
    if _identity_tokens(viewer) & _identity_tokens(owner):
        return True
    remembered = cache if cache is not None else {}
    if owner not in remembered:
        remembered[owner] = reporting_leader_tokens(session, owner)
    return bool(_identity_tokens(viewer, viewer_open_ids) & remembered[owner])


def _is_live_review_row(row: Any, *, todo_only: bool) -> bool:
    if row is None:
        return False
    if int(getattr(row, "is_deleted", 0) or 0) != 0:
        return False
    if _text(getattr(row, "status", None)).upper() == "SUPERSEDED":
        return False
    if _text(getattr(row, "subject_type", None)).upper() != "USER":
        return False
    item_type = _text(getattr(row, "item_type", None))
    allowed = (ITEM_TODO,) if todo_only else (ITEM_EVENT, ITEM_TODO)
    return item_type in allowed


def _matches(
    session: Session,
    row: Optional[CRMSkillExtractItem],
    viewer_oauth_id: str,
    viewer_open_ids: Sequence[str],
    *,
    todo_only: bool,
    cache: dict[str, set[str]],
) -> bool:
    if not _text(viewer_oauth_id) or not _is_live_review_row(row, todo_only=todo_only):
        return False
    return can_manage_uploader(
        session,
        viewer_oauth_id,
        _text(getattr(row, "subject_id", None)),
        viewer_open_ids,
        cache,
    )


def select_visible_rows(
    session: Session,
    rows: Sequence[Any],
    viewer_oauth_id: str,
    viewer_open_ids: Sequence[str] = (),
    *,
    company_manager: bool = False,
) -> tuple[list[Any], bool]:
    """返回可见行，以及当前用户能不能改这些行。公司级管理人员只看。"""
    live = [row for row in rows if _is_live_review_row(row, todo_only=False)]
    cache: dict[str, set[str]] = {}
    manageable = [
        row
        for row in live
        if _matches(
            session,
            row,
            viewer_oauth_id,
            viewer_open_ids,
            todo_only=False,
            cache=cache,
        )
    ]
    visible = live if company_manager else manageable
    visible_ids = {_text(getattr(row, "unique_id", None)) for row in visible}
    manageable_ids = {_text(getattr(row, "unique_id", None)) for row in manageable}
    can_manage = bool(visible_ids) and visible_ids <= manageable_ids
    return visible, can_manage


def load_meeting_review_item(
    session: Session,
    oauth_id: str,
    unique_id: str,
    *,
    todo_only: bool = False,
    open_ids: Sequence[str] = (),
) -> Optional[CRMSkillExtractItem]:
    """上传人或其汇报链上级的经营事项、员工待办。todo_only 时经营事项不算。"""
    item_id = _text(unique_id)
    if not item_id or not _text(oauth_id):
        return None
    row = session.exec(
        select(CRMSkillExtractItem).where(CRMSkillExtractItem.unique_id == item_id)
    ).first()
    if not _matches(session, row, oauth_id, open_ids, todo_only=todo_only, cache={}):
        return None
    return row


def save_meeting_review_feedback(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    user_id: str,
    feedback: Optional[str],
    feedback_comment: Optional[str],
) -> dict[str, Any]:
    """经营事项和员工待办都能赞踩。不改采纳状态。"""
    return save_feedback(
        session,
        row,
        user_id=user_id,
        feedback=feedback,
        feedback_comment=feedback_comment,
    )


def save_meeting_review_decisions(
    session: Session,
    oauth_id: str,
    items: Sequence[Mapping[str, Any]],
    open_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """只采纳或拒绝员工待办。经营事项、无权限的条目 ok 为 false。"""
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
    cache: dict[str, set[str]] = {}
    for item in normalized:
        current = found.get(item["unique_id"])
        if current is None or not _matches(
            session,
            current,
            oauth_id,
            open_ids,
            todo_only=True,
            cache=cache,
        ):
            results.append({"unique_id": item["unique_id"], "ok": False})
            continue
        current.status = item["status"]
        current.adopted_todo_id = item["adopted_todo_id"]
        current.reject_reason = item["reject_reason"]
        current.updated_at = _now()
        session.add(current)
        applied = True
        results.append({"unique_id": item["unique_id"], "ok": True, **interaction_fields(current)})
    if applied:
        session.commit()
    return {"results": results}


def link_meeting_review_task(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    user_id: str,
    todo_id: str,
    adopt: bool,
) -> dict[str, Any]:
    """把已建好的任务挂到员工待办上。adopt 为 true 时同时标为已采纳。"""
    task_id = require_active_todo(session, todo_id)
    append_todo_id(
        session,
        subject_type=SUBJECT_LEADER_MEETING_REVIEW,
        subject_id=meeting_review_anchor(row),
        scene=SCENE_LEADER_MEETING_REVIEW,
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
        "例会复盘关联任务 unique_id=%s todo_id=%s user_id=%s adopt=%s",
        row.unique_id,
        task_id,
        user_id,
        adopt,
    )
    return {
        "unique_id": _text(row.unique_id),
        "todo_id": task_id,
        "adopted": bool(adopt),
        "status": interaction_fields(row)["status"],
    }


def save_meeting_review_comments(
    session: Session,
    row: CRMSkillExtractItem,
    *,
    user_id: str,
    author: str,
    comments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """经营事项和员工待办都能评论、回复。带 id 时只改本人那条的正文。"""
    return save_subject_comments(
        session,
        subject_type=SUBJECT_LEADER_MEETING_REVIEW,
        subject_id=meeting_review_anchor(row),
        scene=SCENE_LEADER_MEETING_REVIEW,
        actor_user_id=_text(user_id),
        author=author,
        comments=comments,
    )


def attach_todo_tasks(session: Session, rows: Sequence[Any], assembled: dict[str, Any]) -> None:
    """评论写进两类条目。已挂上的任务摘要只写进员工待办。"""
    grouped = assembled.get("items") or {}
    items = [item for section in grouped.values() if isinstance(section, list) for item in section]
    if not items:
        return
    by_id = {_text(getattr(row, "unique_id", None)): row for row in rows}
    paired: list[tuple[dict[str, Any], str]] = []
    anchors: list[str] = []
    for item in items:
        row = by_id.get(_text(item.get("unique_id")))
        anchor = meeting_review_anchor(row) if row is not None else ""
        paired.append((item, anchor))
        if anchor:
            anchors.append(anchor)
    threads = load_threads(session, SUBJECT_LEADER_MEETING_REVIEW, anchors)
    todo_ids: list[str] = []
    for thread in threads.values():
        todo_ids.extend(thread.get("todo_ids") or [])
    summaries = load_task_summaries(session, todo_ids)
    for item, anchor in paired:
        thread = threads.get(anchor) or {}
        item["comments"] = list(thread.get("comments") or [])
        if "tasks" not in item:
            continue
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

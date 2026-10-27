"""今日重点查询。销售和负责人都只看自己的 USER 条目。"""

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from app.repositories.user_profile import user_profile_repo
from app.services.department_today_highlights import highlights_for_leader
from app.services.leader_intervention_actions import (
    link_item_task,
    load_actionable_item,
    save_decisions,
    save_feedback,
    save_item_comments,
)
from app.services.sales_immediate_actions import (
    load_own_highlight_item,
    save_sales_highlight_decisions,
)
from app.services.sales_today_highlights import highlights_for_viewer, parse_report_date
from app.services.sales_week_visits import load_week_visit_summary
from app.services.subject_interaction import SubjectInteractionError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/today-highlights"])


def _viewer_oauth_id(db_session: SessionDep, user: CurrentUserDep) -> str:
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    return str(getattr(profile, "oauth_user_id", None) or "").strip() if profile else ""


@router.get("/crm/today-highlights/sales")
def get_sales_today_highlights(
    db_session: SessionDep,
    user: CurrentUserDep,
    report_date: Optional[str] = Query(
        default=None,
        description="业务日 YYYY-MM-DD，默认北京时间当天",
    ),
):
    """当前登录人的今日重点。不接受他人 subject_id。"""
    try:
        target = parse_report_date(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        profile = user_profile_repo.get_by_user_id(db_session, user.id)
        oauth_id = getattr(profile, "oauth_user_id", None) if profile else None
        data = highlights_for_viewer(db_session, oauth_id, target)
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.get("/crm/today-highlights/departments")
def get_department_today_highlights(
    db_session: SessionDep,
    user: CurrentUserDep,
    report_date: Optional[str] = Query(
        default=None,
        description="业务日 YYYY-MM-DD，默认北京时间当天",
    ),
):
    """当前负责人自己的今日需介入和报告。subject_id 是其 oauth id。"""
    try:
        target = parse_report_date(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        data = highlights_for_leader(db_session, user.id, target)
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


class InterventionFeedbackUpdate(BaseModel):
    """一条今日需介入的点赞或点踩。feedback 为空时撤销。"""

    feedback: Optional[Literal["up", "down"]] = Field(
        default=None,
        description="up 点赞，down 点踩，空则撤销",
    )
    feedback_comment: Optional[str] = Field(default=None, max_length=500)


class InterventionDecisionItem(BaseModel):
    unique_id: str = Field(min_length=1, max_length=255)
    action: Literal["adopt", "reject"]
    adopted_todo_id: Optional[str] = Field(default=None, max_length=255)
    reject_reason: Optional[str] = Field(default=None, max_length=1000)


class InterventionDecisionsUpdate(BaseModel):
    items: List[InterventionDecisionItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self) -> "InterventionDecisionsUpdate":
        ids = [item.unique_id.strip() for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("unique_id 不能重复")
        return self


class InterventionCommentIn(BaseModel):
    content: str = Field(max_length=2000)
    reply_to_id: Optional[str] = Field(default=None, max_length=64)
    author_id: Optional[str] = None


class InterventionCommentsUpdate(BaseModel):
    comments: List[InterventionCommentIn] = Field(min_length=1, max_length=20)


class InterventionTaskLink(BaseModel):
    """前端在其他服务建好任务后，把任务 unique_id 写回这条介入。"""

    todo_id: str = Field(min_length=1, max_length=255)
    adopt: bool = False


def _author_name(db_session: SessionDep, user: CurrentUserDep) -> str:
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    return str(getattr(profile, "name", None) or "").strip()


def _require_intervention_item(db_session: SessionDep, user: CurrentUserDep, unique_id: str):
    row = load_actionable_item(db_session, user.id, unique_id)
    if row is None:
        raise HTTPException(status_code=404, detail="需介入条目不存在或无权限")
    return row


@router.post("/crm/today-highlights/sales/items/{unique_id}/feedback")
def save_sales_highlight_feedback(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: InterventionFeedbackUpdate,
):
    """销售更新自己一条今日重点抽取的赞踩。"""
    try:
        row = load_own_highlight_item(db_session, _viewer_oauth_id(db_session, user), unique_id)
        if row is None:
            raise HTTPException(status_code=404, detail="今日重点条目不存在或无权限")
        data = save_feedback(
            db_session,
            row,
            user_id=str(user.id),
            feedback=payload.feedback,
            feedback_comment=payload.feedback_comment,
        )
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/today-highlights/sales/items/decisions")
def save_sales_today_highlight_decisions(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: InterventionDecisionsUpdate,
):
    """销售批量采纳或拒绝自己的今日重点抽取。不是自己的条目 ok 为 false。"""
    try:
        data = save_sales_highlight_decisions(
            db_session,
            _viewer_oauth_id(db_session, user),
            user.id,
            [item.model_dump() for item in payload.items],
        )
        return {"code": 0, "message": "success", "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/today-highlights/departments/items/{unique_id}/feedback")
def save_department_highlight_feedback(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: InterventionFeedbackUpdate,
):
    """负责人更新一条今日需介入的赞踩。"""
    try:
        row = _require_intervention_item(db_session, user, unique_id)
        data = save_feedback(
            db_session,
            row,
            user_id=str(user.id),
            feedback=payload.feedback,
            feedback_comment=payload.feedback_comment,
        )
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/today-highlights/departments/items/decisions")
def save_department_highlight_decisions(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: InterventionDecisionsUpdate,
):
    """负责人批量采纳或拒绝今日需介入。无权限的条目 ok 为 false。"""
    try:
        data = save_decisions(
            db_session,
            user.id,
            [item.model_dump() for item in payload.items],
        )
        return {"code": 0, "message": "success", "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/today-highlights/departments/items/{unique_id}/comments")
def save_department_highlight_comments(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: InterventionCommentsUpdate,
):
    """负责人追加评论。reply_to_id 指向本条已有评论，用来盖楼。"""
    try:
        actor = str(user.id)
        for item in payload.comments:
            if not item.content.strip():
                raise HTTPException(status_code=400, detail="评论内容不能为空")
            if item.author_id and item.author_id.strip() != actor:
                raise HTTPException(status_code=400, detail="author_id 必须是当前用户")
        row = _require_intervention_item(db_session, user, unique_id)
        comments = save_item_comments(
            db_session,
            row,
            user_id=actor,
            author=_author_name(db_session, user),
            comments=[item.model_dump() for item in payload.comments],
        )
        return {"code": 0, "message": "success", "data": {"unique_id": unique_id, "comments": comments}}
    except HTTPException:
        raise
    except SubjectInteractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/today-highlights/departments/items/{unique_id}/tasks")
def link_department_highlight_task(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: InterventionTaskLink,
):
    """把已建好的销售任务挂到这条今日需介入。adopt 为 true 时同时标为已采纳。"""
    try:
        row = _require_intervention_item(db_session, user, unique_id)
        data = link_item_task(
            db_session,
            row,
            user_id=str(user.id),
            todo_id=payload.todo_id,
            adopt=payload.adopt,
        )
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.get("/crm/sales/week-visits")
def get_sales_week_visits(
    db_session: SessionDep,
    user: CurrentUserDep,
    report_date: Optional[str] = Query(
        default=None,
        description="业务日 YYYY-MM-DD，取该日所在周；与 week_start 二选一",
    ),
    week_start: Optional[str] = Query(
        default=None,
        description="周内任意一天 YYYY-MM-DD，优先于 report_date",
    ),
):
    """当前登录人本周拜访总数，以及销售视角复盘按 severity 的数量。"""
    try:
        target = parse_report_date(week_start or report_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        data = load_week_visit_summary(db_session, user.id, target)
        return {"code": 0, "message": "success", "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()

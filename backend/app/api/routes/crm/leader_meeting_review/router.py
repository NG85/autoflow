"""例会复盘卡片上的抽取项。报告列表和正文不在这里。"""

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.deps import CurrentUserDep, SessionDep
from app.exceptions import InternalServerError
from app.repositories.user_profile import user_profile_repo
from app.services.visit_record_insight_reader import collect_profile_open_ids
from app.services.leader_meeting_review_actions import (
    link_meeting_review_task,
    load_meeting_review_item,
    notify_meeting_review_comments,
    save_meeting_review_comments,
    viewer_is_company_manager,
    save_meeting_review_decisions,
    save_meeting_review_feedback,
)
from app.services.subject_interaction import SubjectInteractionError
from app.services.leader_meeting_review_card import empty_extracts, extracts_for_viewer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm", "crm/leader-meeting-review"])

_MISSING = "例会复盘条目不存在或无权限"


def _author_name(db_session: SessionDep, user: CurrentUserDep) -> str:
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    return str(getattr(profile, "name", None) or "").strip()


def _viewer_identity(db_session: SessionDep, user: CurrentUserDep) -> tuple[str, list[str]]:
    profile = user_profile_repo.get_by_user_id(db_session, user.id)
    oauth_id = str(getattr(profile, "oauth_user_id", None) or "").strip() if profile else ""
    return oauth_id, collect_profile_open_ids(profile)


class MeetingReviewFeedbackUpdate(BaseModel):
    """经营事项或员工待办的点赞、点踩。feedback 为空时撤销。"""

    feedback: Optional[Literal["up", "down"]] = Field(
        default=None,
        description="up 点赞，down 点踩，空则撤销",
    )
    feedback_comment: Optional[str] = Field(default=None, max_length=500)


class MeetingReviewDecisionItem(BaseModel):
    unique_id: str = Field(min_length=1, max_length=255)
    action: Literal["adopt", "reject"]
    adopted_todo_id: Optional[str] = Field(default=None, max_length=255)
    reject_reason: Optional[str] = Field(default=None, max_length=1000)


class MeetingReviewDecisionsUpdate(BaseModel):
    items: List[MeetingReviewDecisionItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self) -> "MeetingReviewDecisionsUpdate":
        ids = [item.unique_id.strip() for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("unique_id 不能重复")
        return self


class MeetingReviewCommentIn(BaseModel):
    """新评论不带 id。回复带 reply_to_id。修改带已有 id，且只能改自己写的正文。"""

    content: str = Field(max_length=2000)
    reply_to_id: Optional[str] = Field(default=None, max_length=64)
    id: Optional[str] = Field(default=None, max_length=64)
    author_id: Optional[str] = None


class MeetingReviewCommentsUpdate(BaseModel):
    comments: List[MeetingReviewCommentIn] = Field(min_length=1, max_length=20)
    recipient_user_ids: Optional[List[str]] = Field(
        default=None,
        description="可选。非空时评论提醒只发给这些人（本地 user id）。不传或空列表时，顶层评论发给上传人，回复发给被回复作者。",
    )


class MeetingReviewTaskLink(BaseModel):
    """前端在其他服务建好任务后，把任务 unique_id 写回这条员工待办。"""

    todo_id: str = Field(min_length=1, max_length=255)
    adopt: bool = False


@router.get("/crm/leader-meeting-reviews/{report_id}/extracts")
def get_leader_meeting_review_extracts(
    report_id: str,
    db_session: SessionDep,
    user: CurrentUserDep,
):
    """上传人、汇报链上级可以看和改。公司级管理人员只能看。"""
    try:
        oauth_id, open_ids = _viewer_identity(db_session, user)
        company_manager = viewer_is_company_manager(user.id)
        if not oauth_id and not company_manager:
            data = empty_extracts(report_id.strip())
        else:
            data = extracts_for_viewer(
                db_session,
                report_id,
                oauth_id,
                open_ids,
                company_manager=company_manager,
            )
        return {"code": 0, "message": "success", "data": data}
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/leader-meeting-reviews/items/{unique_id}/feedback")
def save_leader_meeting_review_feedback(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: MeetingReviewFeedbackUpdate,
):
    """更新一条经营事项或员工待办的赞踩。"""
    try:
        oauth_id, open_ids = _viewer_identity(db_session, user)
        row = load_meeting_review_item(db_session, oauth_id, unique_id, open_ids=open_ids)
        if row is None:
            raise HTTPException(status_code=404, detail=_MISSING)
        data = save_meeting_review_feedback(
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


@router.post("/crm/leader-meeting-reviews/items/decisions")
def save_leader_meeting_review_decisions(
    db_session: SessionDep,
    user: CurrentUserDep,
    payload: MeetingReviewDecisionsUpdate,
):
    """采纳或拒绝员工待办。经营事项和别人的条目 ok 为 false。"""
    try:
        oauth_id, open_ids = _viewer_identity(db_session, user)
        data = save_meeting_review_decisions(
            db_session,
            oauth_id,
            [item.model_dump() for item in payload.items],
            open_ids,
        )
        return {"code": 0, "message": "success", "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/leader-meeting-reviews/items/{unique_id}/comments")
def save_leader_meeting_review_comments(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: MeetingReviewCommentsUpdate,
):
    """追加、回复或修改评论。经营事项和员工待办都可以。修改只改本人那条的正文。"""
    try:
        actor = str(user.id)
        for item in payload.comments:
            if not item.content.strip():
                raise HTTPException(status_code=400, detail="评论内容不能为空")
            if item.author_id and item.author_id.strip() != actor:
                raise HTTPException(status_code=400, detail="author_id 必须是当前用户")
        oauth_id, open_ids = _viewer_identity(db_session, user)
        row = load_meeting_review_item(db_session, oauth_id, unique_id, open_ids=open_ids)
        if row is None:
            raise HTTPException(status_code=404, detail=_MISSING)
        author = _author_name(db_session, user)
        incoming = [item.model_dump() for item in payload.comments]
        comments = save_meeting_review_comments(
            db_session,
            row,
            user_id=actor,
            author=author,
            comments=incoming,
        )
        notify_meeting_review_comments(
            db_session,
            row,
            current_user_id=actor,
            author=author,
            comments=incoming,
            saved_comments=comments,
            recipient_user_ids=payload.recipient_user_ids,
        )
        return {"code": 0, "message": "success", "data": {"unique_id": unique_id, "comments": comments}}
    except HTTPException:
        raise
    except SubjectInteractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(exc)
        raise InternalServerError()


@router.post("/crm/leader-meeting-reviews/items/{unique_id}/tasks")
def link_leader_meeting_review_task(
    db_session: SessionDep,
    user: CurrentUserDep,
    unique_id: str,
    payload: MeetingReviewTaskLink,
):
    """把已建好的销售任务挂到这条员工待办。adopt 为 true 时同时标为已采纳。"""
    try:
        oauth_id, open_ids = _viewer_identity(db_session, user)
        row = load_meeting_review_item(
            db_session,
            oauth_id,
            unique_id,
            todo_only=True,
            open_ids=open_ids,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=_MISSING)
        data = link_meeting_review_task(
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

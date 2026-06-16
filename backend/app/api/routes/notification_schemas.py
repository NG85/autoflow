"""统一消息推送 `/notification/push` 请求体：按 type 判别联合，各类型字段独立定义。"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class ReviewSessionContext(BaseModel):
    """review_session：阶段与 session 标识。"""

    stage: str = Field(..., description="CRMReviewSession.stage，如 initial_edit / lead_review")
    session_id: str = Field(..., description="CRM review session_id")


class DailyNoFollowupReminderPushRequest(BaseModel):
    """当日无跟进提醒：按实际拜访日期 visit_communication_date 检查，无记录则推送。"""

    type: Literal["daily_no_followup_reminder"] = "daily_no_followup_reminder"
    recipient_user_ids: Optional[List[str]] = Field(
        default=None,
        description="可选；未传则取 role=sales 的活跃销售，传入时亦仅保留 sales 角色",
    )
    check_date: Optional[str] = Field(
        default=None,
        description="检查日期 YYYY-MM-DD，默认北京时间当天",
    )


class WeeklyFollowupCommentPushRequest(BaseModel):
    """周跟进总结评论提醒（文本消息）。"""

    type: Literal["weekly_followup_comment"] = "weekly_followup_comment"
    recipient_user_ids: List[str]
    author_name: Optional[str] = None
    jump_url: Optional[str] = None
    link_text: Optional[str] = None
    content: Optional[str] = None


class VisitRecordCommentPushRequest(BaseModel):
    """拜访记录评论提醒（文本消息）。"""

    type: Literal["visit_record_comment"] = "visit_record_comment"
    recipient_user_ids: List[str]
    author_name: Optional[str] = None
    jump_url: Optional[str] = None
    link_text: Optional[str] = None
    content: Optional[str] = None


class SalesTaskCreatedPushRequest(BaseModel):
    """外部服务创建销售任务后推送（文本消息）。"""

    type: Literal["sales_task_created"] = "sales_task_created"
    recipient_user_ids: List[str]
    task_id: str
    author_name: Optional[str] = Field(default=None, description="创建人")
    created_at: Optional[str] = Field(default=None, description="创建时间")
    content: Optional[str] = Field(default=None, description="任务详情（含截止时间）")
    link_text: Optional[str] = Field(default=None, description="客户/商机文案，可选")
    jump_url: Optional[str] = Field(
        default=None,
        description="未传时兜底 CRM_SALES_TASK_PAGE_URL/{task_id}",
    )
    task_count: int = Field(default=1, ge=1)


class ReviewSessionPushRequest(BaseModel):
    """review 阶段推进触发的推送；接收人由 CRMReviewAttendee 解析。"""

    type: Literal["review_session"] = "review_session"
    context: ReviewSessionContext


class VisitRecordCardPushRequest(BaseModel):
    """Aldebaran 拜访后处理完成：推送拜访卡片。"""

    type: Literal["visit_record_card"] = "visit_record_card"
    record_id: str
    visit_tasks: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description=(
            "拜访卡片 tasks，每项含 task_status、task_title、task_id；"
            "未传或空则 task_count=0"
        ),
    )
    is_revised: bool = Field(
        default=False,
        description="修改后的重推卡片",
    )
    revision_seq: Optional[int] = Field(
        default=None,
        description="修订序号，与 crm_sales_visit_records_revisions.revision_seq 对应",
    )


PushNotificationRequest = Annotated[
    Union[
        WeeklyFollowupCommentPushRequest,
        VisitRecordCommentPushRequest,
        SalesTaskCreatedPushRequest,
        ReviewSessionPushRequest,
        VisitRecordCardPushRequest,
        DailyNoFollowupReminderPushRequest,
    ],
    Field(discriminator="type"),
]

"""统一消息推送 `/notification/push` 请求体：按 type 判别联合，各类型字段独立定义。"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ReviewSessionContext(BaseModel):
    """review_session：阶段与 session 标识。"""

    stage: str = Field(..., description="CRMReviewSession.stage，如 initial_edit / lead_review")
    session_id: str = Field(..., description="CRM review session_id")
    session_type: Optional[str] = Field(
        default=None,
        description="sales_update | lead_analysis | cxo | legacy_long",
    )


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
        description="未传时兜底 {REVIEW_REPORT_HOST}{CRM_SALES_TASK_PAGE_URL}/{task_id}",
    )
    task_count: int = Field(default=1, ge=1)


class ReviewSessionPushRequest(BaseModel):
    """review 阶段推进触发的推送；接收人由 CRMReviewAttendee 解析。"""

    type: Literal["review_session"] = "review_session"
    context: ReviewSessionContext


class LeaderMeetingReviewCardPushRequest(BaseModel):
    """Aldebaran 例会复盘抽取完成：按抽取项给业务负责人和提交人发卡片。"""

    type: Literal["leader_meeting_review_card"] = "leader_meeting_review_card"
    report_id: str = Field(..., min_length=1, description="bi_reports.report_id")
    owner_id: str = Field(
        ...,
        min_length=1,
        description="业务负责人 oauth id，也是抽取行 subject_id",
    )
    creator_id: Optional[str] = Field(
        default=None,
        description="提交人 oauth id。与 owner_id 不同时再发一张同样的卡",
    )
    report_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD",
    )


def _dedupe_ids(values: List[str]) -> List[str]:
    found: List[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in found:
            found.append(text)
    return found


class LeaderMeetingReviewCxoCardPushRequest(BaseModel):
    """Aldebaran 例会管理评价落库后：把摘要和抽取按钮推给 CXO。"""

    type: Literal["leader_meeting_review_cxo_card"] = "leader_meeting_review_cxo_card"
    insight_ids: List[str] = Field(
        ...,
        min_length=1,
        description="crm_entity_insight.unique_id。多条时只取最新的管理评价",
    )
    recipient_user_ids: Optional[List[str]] = Field(
        default=None,
        description="可选本地 user_id，从 CXO 今日重点的接收人里收窄；不能扩大名单。不传则与 CXO 今日重点相同",
    )
    report_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD",
    )
    source_report_id: Optional[str] = Field(
        default=None,
        description="原例会复盘 bi_reports.report_id。完整评价报告和原复盘报告都用它打开这场会，不按日期",
    )

    @model_validator(mode="after")
    def require_ids(self) -> "LeaderMeetingReviewCxoCardPushRequest":
        self.insight_ids = _dedupe_ids(self.insight_ids)
        if self.recipient_user_ids is not None:
            self.recipient_user_ids = _dedupe_ids(self.recipient_user_ids)
        if not self.insight_ids:
            raise ValueError("insight_ids are required")
        return self


class VisitOpportunityReviewPushRequest(BaseModel):
    """Aldebaran 本次拜访商机评估：Markdown 推给拜访卡片的同一批接收人。"""

    type: Literal["visit_opportunity_review"] = "visit_opportunity_review"
    record_id: str = Field(..., min_length=1, description="拜访记录 record_id，用于解析接收人")
    content: str = Field(..., min_length=1, description="商机评估 Markdown")
    title: Optional[str] = Field(
        default=None,
        description="卡片标题；缺省时飞书取正文首行",
    )
    opportunity_id: Optional[str] = Field(default=None, description="本次拜访关联的商机")
    report_id: Optional[str] = Field(default=None, description="商机评估报告 ID")
    content_type: Literal["markdown"] = "markdown"
    delivery: Literal["card", "post"] = Field(
        default="card",
        description="飞书/Lark：card=无模板卡片，post=富文本；钉钉与企微忽略",
    )


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


class SalesTodayHighlightsPushRequest(BaseModel):
    """一线销售今日重点：读 crm_skill_extract_items，只推本人。"""

    type: Literal["sales_today_highlights"] = "sales_today_highlights"
    report_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD；不传则北京时间当天",
    )
    recipient_user_ids: Optional[List[str]] = Field(
        default=None,
        description="可选 OAuth id；不传则发给当天有条目的全部一线销售",
    )


class CompanyTodayHighlightsPushRequest(BaseModel):
    """CXO 今日重点：读公司人效和重点商机。优先发给策略名单，没配名单时发给公司日报接收人。哪天发由调度配置决定。"""

    type: Literal["company_today_highlights"] = "company_today_highlights"
    report_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD，推送日；不传则北京时间当天",
    )
    recipient_user_ids: Optional[List[str]] = Field(
        default=None,
        description="可选本地 user_id，从这次的推送接收人里收窄；不能扩大名单。名单没配时收窄的是公司日报接收人",
    )


class DepartmentTodayHighlightsPushRequest(BaseModel):
    """销售负责人今日重点：按负责人 oauth 推 TODAY_INTERVENTION，一人一天一张卡。"""

    type: Literal["department_today_highlights"] = "department_today_highlights"
    report_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD；不传则北京时间当天",
    )
    recipient_user_ids: Optional[List[str]] = Field(
        default=None,
        description="可选 OAuth id；不传则发给当天有卡片的全部负责人",
    )


class DailyVisitReportPushRequest(BaseModel):
    """公司/部门日拜访报告 Markdown：读 crm_department_daily_summary.summary_content。"""

    type: Literal["daily_visit_report"] = "daily_visit_report"
    scene: str = Field(
        default="company_daily",
        description="company_daily | department_daily",
    )
    variant: str = Field(default="summary_md", description="目前仅 summary_md")
    report_date: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD；不传则昨天（北京日期，与日报 cron 一致）",
    )
    title: Optional[str] = Field(
        default=None,
        description="可选标题；默认「APTSell 销售经营日报｜{date}」",
    )
    department_id: Optional[str] = Field(
        default=None,
        description="部门日报：部门 ID；与 department_name 都空则推该日有负责人或配置了部门日报群的部门",
    )
    department_name: Optional[str] = Field(
        default=None,
        description="部门日报：部门名称",
    )
    delivery: Literal["card", "post"] = Field(
        default="card",
        description="飞书/Lark：card=无模板卡片，post=富文本；钉钉忽略",
    )


class WeeklyVisitReportPushRequest(BaseModel):
    """公司/部门周拜访报告 Markdown：读 crm_weekly_followup_summary（report_kind=visit_report）。"""

    type: Literal["weekly_visit_report"] = "weekly_visit_report"
    scene: str = Field(
        ...,
        description="company_weekly | department_weekly",
    )
    variant: str = Field(default="visit_report", description="目前仅 visit_report")
    week_start: Optional[str] = Field(
        default=None,
        description="周开始日期 YYYY-MM-DD；与 week_end 都空则上一完整周",
    )
    week_end: Optional[str] = Field(
        default=None,
        description="周结束日期 YYYY-MM-DD",
    )
    title: Optional[str] = Field(
        default=None,
        description="可选标题；默认「APTSell 销售经营周报｜{week_start}至{week_end}」",
    )
    department_id: Optional[str] = Field(
        default=None,
        description="部门周报：部门 ID；与 department_name 都空则推该周有负责人或配置了部门日报群的部门",
    )
    department_name: Optional[str] = None
    delivery: Literal["card", "post"] = Field(
        default="card",
        description="飞书/Lark：card=无模板卡片，post=富文本；钉钉忽略",
    )


class NotificationPreferenceUpdateRequest(BaseModel):
    scene: str
    receive: bool
    variant: str = ""
    department_id: Optional[str] = None
    department_name: Optional[str] = None


class PlatformNotificationPushRequest(BaseModel):
    """平台通知：向指定用户推送纯文本或 Markdown 消息。"""

    type: Literal["platform_notification"] = "platform_notification"
    recipient_user_ids: List[str] = Field(..., description="接收人 user_id 集合")
    content_type: Literal["text", "markdown"] = Field(
        ...,
        description="text=纯文本；markdown=Markdown（飞书/Lark 按 delivery 投递）",
    )
    content: str = Field(..., min_length=1, description="通知正文")
    title: Optional[str] = Field(
        default=None,
        description="可选标题；card header / post title 优先用此字段，否则取正文首行",
    )
    delivery: Literal["card", "post"] = Field(
        default="card",
        description=(
            "飞书/Lark 投递形态（均正式支持）："
            "card=interactive 无模板卡片（markdown 用 schema 2.0；text 仍走 text 消息）；"
            "post=富文本（markdown 用 md 标签，text 用 text 标签）。"
            "钉钉忽略此字段，统一 sampleMarkdown"
        ),
    )


PushNotificationRequest = Annotated[
    Union[
        WeeklyFollowupCommentPushRequest,
        VisitRecordCommentPushRequest,
        SalesTaskCreatedPushRequest,
        ReviewSessionPushRequest,
        LeaderMeetingReviewCardPushRequest,
        LeaderMeetingReviewCxoCardPushRequest,
        VisitOpportunityReviewPushRequest,
        VisitRecordCardPushRequest,
        DailyNoFollowupReminderPushRequest,
        PlatformNotificationPushRequest,
        WeeklyVisitReportPushRequest,
        DailyVisitReportPushRequest,
        SalesTodayHighlightsPushRequest,
        DepartmentTodayHighlightsPushRequest,
        CompanyTodayHighlightsPushRequest,
    ],
    Field(discriminator="type"),
]

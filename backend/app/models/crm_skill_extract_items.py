"""Aldebaran 日 skill 结构化抽取。表已存在，Autoflow 不建表、不迁移。

正文由 Aldebaran 写入。本服务可读，并更新赞踩与采纳列。
一线销售今日重点按 (report_date, USER, oauth_user_id) 查询。
"""

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, Date, Text
from sqlmodel import Field, SQLModel


class CRMSkillExtractItem(SQLModel, table=True):
    __tablename__ = "crm_skill_extract_items"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键")
    unique_id: str = Field(max_length=255, unique=True, description="唯一性ID")
    source_kind: str = Field(max_length=64, description="immediate_action | opportunity_risk | 后续 skill")
    source_report_unique_id: Optional[str] = Field(
        default=None, max_length=255, description="来源日报告行 unique_id"
    )
    report_date: date = Field(sa_column=Column(Date, nullable=False), description="报告业务日")
    subject_type: str = Field(max_length=32, description="USER | DEPARTMENT")
    subject_id: str = Field(max_length=255, description="USER=oauth_user_id")
    audience: str = Field(max_length=16, description="sales | leader")
    recipient_user_id: Optional[str] = Field(
        default=None, max_length=255, description="USER 行=该销售 oauth_user_id"
    )
    department_id: Optional[str] = Field(default=None, max_length=255, description="所属部门")
    account_id: Optional[str] = Field(default=None, max_length=255, description="关联客户ID")
    opportunity_id: Optional[str] = Field(default=None, max_length=255, description="关联商机ID")
    item_type: str = Field(max_length=64, description="抽取类型")
    extract_key: str = Field(max_length=64, description="类型内稳定键")
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False), description="抽取正文")
    profile_id: Optional[str] = Field(default=None, max_length=64, description="抽取 profile id")
    card_links: Optional[Any] = Field(
        default=None, sa_column=Column(JSON, nullable=True), description="入口快照"
    )
    schema_version: int = Field(default=1, description="payload schema 版本")
    status: str = Field(default="PENDING", max_length=32, description="PENDING | SUPERSEDED | ADOPTED | REJECTED")
    adopted_todo_id: Optional[str] = Field(default=None, max_length=255, description="采纳后 todo unique_id")
    reject_reason: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True), description="不采纳原因")
    feedback: Optional[str] = Field(default=None, max_length=16, description="up | down")
    feedback_comment: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True), description="赞踩备注"
    )
    feedback_user_id: Optional[str] = Field(default=None, max_length=255, description="反馈用户")
    feedback_at: Optional[datetime] = Field(default=None, description="反馈时间")
    is_deleted: int = Field(default=0, description="软删除标识")
    created_at: Optional[datetime] = Field(default=None, description="创建时间")
    updated_at: Optional[datetime] = Field(default=None, description="更新时间")

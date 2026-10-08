"""Aldebaran 拜访复盘结构化抽取。表已存在，Autoflow 不建表、不迁移。

抽取正文由 Aldebaran 写入。Autoflow 更新赞踩，以及采纳/拒绝。
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


class CRMPostvisitExtractItem(SQLModel, table=True):
    __tablename__ = "crm_postvisit_extract_items"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键")
    unique_id: str = Field(max_length=255, unique=True, description="唯一性ID")
    visit_id: str = Field(max_length=255, description="crm_sales_visit_records.record_id")
    source_insight_unique_id: Optional[str] = Field(
        default=None, max_length=255, description="来源 crm_entity_insight.unique_id"
    )
    account_id: Optional[str] = Field(default=None, max_length=255, description="关联客户ID")
    opportunity_id: Optional[str] = Field(default=None, max_length=255, description="关联商机ID")
    viewer_role: str = Field(default="sales", max_length=16, description="sales")
    insight_type: Optional[str] = Field(default=None, max_length=64, description="来源 insight_type")
    item_type: str = Field(max_length=64, description="抽取类型")
    extract_key: str = Field(max_length=64, description="类型内稳定键")
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False), description="抽取正文")
    severity: Optional[str] = Field(default=None, max_length=16, description="抄自 insight.severity")
    profile_id: Optional[str] = Field(default=None, max_length=64, description="抽取 profile id")
    card_links: Optional[Any] = Field(default=None, sa_column=Column(JSON, nullable=True), description="入口快照")
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

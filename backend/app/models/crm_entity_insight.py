"""Aldebaran 实体洞察。表已存在，Autoflow 不建表、不迁移。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class CRMEntityInsight(SQLModel, table=True):
    __tablename__ = "crm_entity_insight"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键")
    unique_id: str = Field(max_length=255, unique=True, description="唯一性ID")
    entity_type: str = Field(max_length=32, description="实体类型：ACCOUNT / OPPORTUNITY / VISIT")
    entity_id: str = Field(max_length=255, description="实体唯一ID")
    account_id: Optional[str] = Field(default=None, max_length=255, description="关联账户ID")
    opportunity_id: Optional[str] = Field(default=None, max_length=255, description="关联商机ID")
    insight_type: str = Field(max_length=64, description="洞察类型")
    category: Optional[str] = Field(default=None, max_length=64, description="分类标签")
    severity: Optional[str] = Field(default=None, max_length=16, description="严重程度：HIGH / MEDIUM / LOW")
    title: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True), description="洞察标题")
    summary: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True), description="单行摘要")
    detail_text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True), description="完整洞察内容")
    evidence: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True), description="支撑数据 / 来源引用")
    source: Optional[str] = Field(default=None, max_length=128, description="来源")
    source_execution_id: Optional[str] = Field(default=None, max_length=255, description="来源工作流 execution_id")
    source_session_id: Optional[str] = Field(default=None, max_length=255, description="来源复盘会话 session_id")
    generated_at: datetime = Field(description="洞察生成时间")
    expires_at: Optional[datetime] = Field(default=None, description="过期时间，空则永不过期")
    is_deleted: int = Field(default=0, description="软删除标识")
    created_at: Optional[datetime] = Field(default=None, description="创建时间")
    updated_at: Optional[datetime] = Field(default=None, description="更新时间")

from typing import Optional
from datetime import datetime, date
from sqlalchemy import Index
from sqlmodel import Field, Column, DateTime, Date, Relationship, SQLModel, Text

class CRMOpportunityUpdates(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
        
    """商机更新记录表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: str = Field(max_length=255, description="关联的商机唯一ID")
    opportunity_name: Optional[str] = Field(nullable=True, max_length=255, description="商机名称")
    record_date: date = Field(sa_column=Column(Date), description="记录日期（YYYY-MM-DD）")
    update_type: Optional[str] = Field(nullable=True, max_length=50, description="更新类型（日常/周报/会议/电话/邮件）")
    data_source: Optional[str] = Field(nullable=True, max_length=255, description="数据来源")
    update_date: Optional[datetime] = Field(sa_column=Column(DateTime, default=None, nullable=True), description="更新的具体时间戳")
    creator: Optional[str] = Field(nullable=True, max_length=255, description="创建人")
    creator_id: Optional[str] = Field(nullable=True, max_length=255, description="创建人唯一性ID")
    summary: Optional[str] = Field(nullable=True, max_length=500, description="更新摘要")
    detailed_notes: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="详细描述和进展")
    next_steps: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步行动计划")
    key_stakeholders: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="相关关键干系人")
    customer_sentiment: Optional[str] = Field(nullable=True, max_length=50, description="客户态度（积极/中性/消极）")
    deal_probability_change: Optional[str] = Field(nullable=True, max_length=50, description="成单概率变化（上升/下降/不变）")
    blockers: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="当前障碍或挑战")
    create_time: datetime = Field(
        sa_column=Column(DateTime, server_default="CURRENT_TIMESTAMP"),
        description="创建时间"
    )
    last_modified_time: datetime = Field(
        sa_column=Column(DateTime, server_default="CURRENT_TIMESTAMP", onupdate="CURRENT_TIMESTAMP"),
        description="最后修改时间"
    )

    __tablename__ = "crm_opportunity_updates"
    
    opportunity: Optional["CRMOpportunity"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(CRMOpportunityUpdates.opportunity_id==CRMOpportunity.unique_id)",
            "foreign_keys": "[CRMOpportunityUpdates.opportunity_id]",
            "viewonly": True
        }
    )
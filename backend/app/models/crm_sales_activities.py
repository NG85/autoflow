from typing import Optional, List
from datetime import datetime, date
from sqlalchemy import Index, select, String
from sqlmodel import Field, Column, DateTime, Date, SQLModel, Text, Session, Relationship

class CRMSalesActivities(SQLModel, table=True):
    class Config:
        orm_mode = True
        read_only = True
        
    """销售活动记录表"""
    id: Optional[int] = Field(default=None, primary_key=True)
    unique_id: str = Field(max_length=255, description="唯一性ID（必填）")
    category: Optional[str] = Field(nullable=True, max_length=50, description="活动类别（如update/callhigh/其他）")
    data_source: Optional[str] = Field(nullable=True, max_length=100, description="数据来源（URL/飞书/CRM/Chatbot/等）")
    record_date: date = Field(sa_column=Column(Date), description="记录日期（YYYY-MM-DD）")
    location: Optional[str] = Field(nullable=True, max_length=255, description="活动地点")
    communication_medium: str = Field(max_length=50, description="活动形式及沟通方式（如：线上会议/线下会议/电话/邮件/拜访/演示/提案等）")
    internal_participants: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="内部参与者（JSON格式）")
    external_participants: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="外部参与者即客户（JSON格式）")
    key_stakeholders: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="关键干系人")
    account_id: Optional[str] = Field(nullable=True, max_length=255, description="关联的客户ID")
    account_name: Optional[str] = Field(nullable=True, max_length=255, description="关联的客户名称")
    opportunity_id: Optional[str] = Field(nullable=True, max_length=255, description="关联的商机ID")
    opportunity_name: Optional[str] = Field(nullable=True, max_length=255, description="关联的商机名称")
    linked_account_ids: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="关联的客户ID列表（JSON格式）")
    linked_opportunity_ids: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="关联的商机ID列表（JSON格式）")
    summary: str = Field(max_length=500, description="活动摘要")
    detailed_notes: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="详细记录")
    next_steps: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步行动计划")
    blockers: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="当前障碍或挑战")
    core_biz_info: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="核心业务信息")
    deal_probability_change: Optional[str] = Field(nullable=True, max_length=50, description="成单概率变化（上升/下降/不变）")
    customer_sentiment: Optional[str] = Field(nullable=True, max_length=50, description="客户态度（积极/中性/消极）")
    correlation_id: Optional[str] = Field(nullable=True, max_length=255, description="关联ID")
    creator: Optional[str] = Field(nullable=True, max_length=255, description="创建人")
    creator_id: Optional[str] = Field(nullable=True, max_length=255, description="创建人唯一性ID")
    create_time: datetime = Field(
        sa_column=Column(DateTime, server_default="CURRENT_TIMESTAMP"),
        description="创建时间"
    )
    last_modifier: Optional[str] = Field(nullable=True, max_length=255, description="最后修改人")
    last_modified_time: datetime = Field(
        sa_column=Column(DateTime, server_default="CURRENT_TIMESTAMP", onupdate="CURRENT_TIMESTAMP"),
        description="最后修改时间"
    )

    __tablename__ = "crm_sales_activities"
    
    # 添加索引
    __table_args__ = (
        Index('idx_activity_type', 'communication_medium'),
        Index('idx_record_date', 'record_date'),
        Index('idx_correlation_id', 'correlation_id'),
        Index('idx_account_id', 'account_id'),
        Index('idx_unique_id', 'unique_id'),
        Index('idx_opportunity_id', 'opportunity_id'),
    )
    
    opportunity: Optional["CRMOpportunity"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "and_(CRMSalesActivities.opportunity_id==cast(CRMOpportunity.unique_id, String))",
            "foreign_keys": "[CRMSalesActivities.opportunity_id]",
            "viewonly": True
        }
    )
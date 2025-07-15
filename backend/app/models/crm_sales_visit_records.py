from datetime import date, datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime, Text, Index

class CRMSalesVisitRecord(SQLModel, table=True):
    __tablename__ = "crm_sales_visit_records"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    account_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称")
    account_id: Optional[str] = Field(nullable=True, max_length=255, description="客户ID")
    opportunity_name: Optional[str] = Field(nullable=True, max_length=255, description="商机名称")
    opportunity_id: Optional[str] = Field(nullable=True, max_length=255, description="商机ID")
    partner_name: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    customer_lead_source: Optional[str] = Field(nullable=True, max_length=255, description="客户/线索来源")
    visit_object_category: Optional[str] = Field(nullable=True, max_length=255, description="拜访对象类别")
    contact_position: Optional[str] = Field(nullable=True, max_length=255, description="客户职位")
    contact_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名字")
    recorder: Optional[str] = Field(nullable=True, max_length=255, description="记录人")
    collaborative_participants: Optional[str] = Field(nullable=True, max_length=255, description="协同参与人")
    visit_communication_date: Optional[date] = Field(nullable=True, description="拜访及沟通日期")
    counterpart_location: Optional[str] = Field(nullable=True, max_length=255, description="拜访地点")
    visit_communication_method: Optional[str] = Field(nullable=True, max_length=255, description="拜访及沟通方式")
    communication_duration: Optional[str] = Field(nullable=True, max_length=255, description="沟通时长")
    expectation_achieved: Optional[str] = Field(nullable=True, max_length=255, description="是/否达成预期")
    followup_record: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录")
    followup_quality_level: Optional[str] = Field(nullable=True, max_length=100, description="跟进记录等级")
    followup_quality_reason: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录评判依据")
    next_steps: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划")
    next_steps_quality_level: Optional[str] = Field(nullable=True, max_length=100, description="下一步计划等级")
    next_steps_quality_reason: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划评判依据")
    attachment: Optional[str] = Field(nullable=True, max_length=255, description="附件")
    parent_record: Optional[str] = Field(nullable=True, max_length=255, description="父记录")
    last_modified_time: Optional[datetime] = Field(nullable=True, description="最后修改时间")
    record_id: Optional[str] = Field(nullable=True, max_length=100, description="记录id")

    __table_args__ = (
        Index("idx_account_name", "account_name"),
        Index("idx_recorder", "recorder"),
        Index("idx_visit_date", "visit_communication_date"),
    )
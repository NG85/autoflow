from datetime import date, datetime
from typing import Optional
from uuid import UUID
from sqlmodel import SQLModel, Field, Column, DateTime, Text, Index
from app.models.crm_dynamic_fields import CRMDynamicFieldsMixin

class CRMSalesVisitRecord(SQLModel, CRMDynamicFieldsMixin, table=True):
    __tablename__ = "crm_sales_visit_records"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    account_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名称")
    account_id: Optional[str] = Field(nullable=True, max_length=255, description="客户ID")
    opportunity_name: Optional[str] = Field(nullable=True, max_length=255, description="商机名称")
    opportunity_id: Optional[str] = Field(nullable=True, max_length=255, description="商机ID")
    partner_name: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴")
    partner_id: Optional[str] = Field(nullable=True, max_length=255, description="合作伙伴ID")
    customer_lead_source: Optional[str] = Field(nullable=True, max_length=255, description="客户/线索来源")
    visit_object_category: Optional[str] = Field(nullable=True, max_length=255, description="拜访对象类别")
    contact_position: Optional[str] = Field(nullable=True, max_length=255, description="客户职位")
    contact_name: Optional[str] = Field(nullable=True, max_length=255, description="客户名字")
    recorder: Optional[str] = Field(nullable=True, max_length=255, description="记录人")
    recorder_id: Optional[UUID] = Field(nullable=True, description="记录人ID")
    collaborative_participants: Optional[str] = Field(
        sa_column=Column(Text, nullable=True), 
        description="协同参与人，TEXT格式存储，支持JSON数组或字符串格式，向后兼容"
    )
    visit_communication_date: Optional[date] = Field(nullable=True, description="拜访及沟通日期")
    counterpart_location: Optional[str] = Field(nullable=True, max_length=255, description="拜访地点")
    visit_communication_method: Optional[str] = Field(nullable=True, max_length=255, description="拜访及沟通方式")
    communication_duration: Optional[str] = Field(nullable=True, max_length=255, description="沟通时长")
    expectation_achieved: Optional[str] = Field(nullable=True, max_length=255, description="是/否达成预期")
    followup_record: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录")
    followup_record_zh: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录（中文版）")
    followup_record_en: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录（英文版）")
    followup_content: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进内容（简易版表单使用）")
    followup_quality_level_zh: Optional[str] = Field(nullable=True, max_length=100, description="跟进记录等级（中文版）")
    followup_quality_level_en: Optional[str] = Field(nullable=True, max_length=100, description="跟进记录等级（英文版）")
    followup_quality_reason_zh: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录评判依据")
    followup_quality_reason_en: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="跟进记录评判依据（英文版）")
    next_steps: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划")
    next_steps_zh: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划（中文版）")
    next_steps_en: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划（英文版）")
    next_steps_quality_level_zh: Optional[str] = Field(nullable=True, max_length=100, description="下一步计划等级")
    next_steps_quality_level_en: Optional[str] = Field(nullable=True, max_length=100, description="下一步计划等级（英文版）")
    next_steps_quality_reason_zh: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划评判依据")
    next_steps_quality_reason_en: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="下一步计划评判依据（英文版）")
    attachment: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="附件")
    parent_record: Optional[str] = Field(nullable=True, max_length=255, description="父记录")
    remarks: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="备注")
    last_modified_time: Optional[datetime] = Field(nullable=True, description="最后修改时间")
    record_id: Optional[str] = Field(nullable=True, max_length=100, description="记录id")
    is_first_visit: Optional[bool] = Field(nullable=True, description="是否首次拜访")
    is_call_high: Optional[bool] = Field(nullable=True, description="是否call high")
    visit_type: Optional[str] = Field(nullable=True, max_length=20, description="拜访类型：form(用户填报)、link(非结构化链接/文件)")
    visit_url: Optional[str] = Field(sa_column=Column(Text, nullable=True), description="会议链接或文件URL")
    subject: Optional[str] = Field(nullable=True, max_length=50, description="拜访主题")
   
    __table_args__ = (
        Index("idx_account_name", "account_name"),
        Index("idx_recorder", "recorder"),
        Index("idx_visit_date", "visit_communication_date"),
        Index("idx_is_first_visit", "is_first_visit"),
        Index("idx_subject", "subject")
    )
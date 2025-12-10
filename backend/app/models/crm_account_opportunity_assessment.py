from typing import Optional
from datetime import datetime, date
from sqlmodel import Field, Column, DateTime, func, SQLModel, String, Date, Boolean, Text


class CRMAccountOpportunityAssessment(SQLModel, table=True):
    """客户商机评估表（只读）"""
    
    class Config:
        orm_mode = True
        
    __tablename__ = 'crm_account_opportunity_assessment'

    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    unique_id: str = Field(sa_column=Column(String(255), nullable=False), description="唯一性ID（必填）")
    assessment_date: date = Field(sa_column=Column(Date, nullable=False), description="评估日期")
    account_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="客户ID")
    account_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="客户名称")
    opportunity_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="商机ID（NULL表示未关联商机）")
    opportunity_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="商机名称（NULL表示未关联商机）")
    assessment_flag: Optional[str] = Field(default=None, sa_column=Column(Text), description="评估结果(red/yellow/green)")
    assessment_description: Optional[str] = Field(default=None, sa_column=Column(Text), description="评估描述")
    assessment_description_en: Optional[str] = Field(default=None, sa_column=Column(Text), description="评估描述(英文)")
    customer_type: Optional[str] = Field(default=None, sa_column=Column(String(50)), description="客户类型(end_customer/partner)")
    account_level: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="客户等级")
    is_first_visit: Optional[bool] = Field(default=None, sa_column=Column(Boolean), description="是否首次拜访")

    follow_up_note: Optional[str] = Field(default=None, sa_column=Column(Text), description="销售跟进记录")
    follow_up_note_en: Optional[str] = Field(default=None, sa_column=Column(Text), description="销售跟进记录(英文)")
    follow_up_next_step: Optional[str] = Field(default=None, sa_column=Column(Text), description="销售跟进下一步")
    follow_up_next_step_en: Optional[str] = Field(default=None, sa_column=Column(Text), description="销售跟进下一步(英文)")

    correlation_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="关联ID，用于链接到部门/公司汇总")

    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
        description="创建时间"
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
        description="更新时间"
    )

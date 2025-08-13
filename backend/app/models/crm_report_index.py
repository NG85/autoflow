from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime, date


class CRMReportIndex(SQLModel, table=True):
    """CRM报告索引表"""
    
    __tablename__ = "crm_report_index"
    
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID")
    unique_id: str = Field(max_length=255, description="唯一性ID")
    report_id: int = Field(description="报告ID, 对应CRMReport表的id")
    report_id_code: str = Field(max_length=255, description="报告ID编码, 对应CRMReport表的report_id")
    execution_id: str = Field(max_length=255, description="执行ID")
    plan_id: str = Field(max_length=255, description="计划ID")
    report_type: str = Field(max_length=64, description="报告类型: review1/review1s/review2/review5/previsit/daily")
    report_calendar_type: str = Field(max_length=64, description="报告周期类型: Daily/Weekly/Monthly/Quarterly/Yearly")
    report_status: str = Field(max_length=32, description="报告状态: draft/published/archived")
    report_date: date = Field(description="报告日期")
    report_datetime: datetime = Field(description="报告精确时间")
    report_week_of_year: int = Field(description="年内周数(1-53)")
    report_month_of_year: int = Field(description="年内月份(1-12)")
    report_quarter_of_year: int = Field(description="年内季度(1-4)")
    report_year: int = Field(description="报告年份")
    created_by: Optional[str] = Field(max_length=255, default=None, description="创建人")
    department_id: Optional[str] = Field(max_length=255, default=None, description="部门ID")
    department_name: Optional[str] = Field(max_length=255, default=None, description="部门名字")
    create_time: datetime = Field(default_factory=datetime.now, description="创建时间")
    update_time: datetime = Field(default_factory=datetime.now, description="更新时间")

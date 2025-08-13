from typing import Optional
from datetime import datetime, date
from sqlmodel import Field, Column, DateTime, func, SQLModel, Integer, String, Date


class CRMDailyAccountStatistics(SQLModel, table=True):
    """销售个人日报统计表 - 对应现有数据表 crm_daily_account_statistics"""
    
    class Config:
        orm_mode = True
        
    __tablename__ = 'crm_daily_account_statistics'

    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    unique_id: str = Field(sa_column=Column(String(255), nullable=False), description="唯一性ID")
    report_date: date = Field(sa_column=Column(Date, nullable=False), description="日期")

    sales_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="销售ID")
    sales_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="销售名字")
    department_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="部门ID")
    department_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="部门名字")

    # Assessment statistics
    assessment_red_count: Optional[int] = Field(default=None, sa_column=Column(Integer), description="评估为red的次数")
    assessment_yellow_count: Optional[int] = Field(default=None, sa_column=Column(Integer), description="评估为yellow的次数")
    assessment_green_count: Optional[int] = Field(default=None, sa_column=Column(Integer), description="评估为green的次数")

    # 最终客户跟进统计
    end_customer_total_follow_up: Optional[int] = Field(default=None, sa_column=Column(Integer), description="总跟进最终客户数")
    end_customer_total_first_visit: Optional[int] = Field(default=None, sa_column=Column(Integer), description="总首次拜访最终客户数")
    end_customer_total_multi_visit: Optional[int] = Field(default=None, sa_column=Column(Integer), description="总多次拜访最终客户数")

    # 合作伙伴跟进统计
    partner_total_follow_up: Optional[int] = Field(default=None, sa_column=Column(Integer), description="总跟进合作伙伴数")
    partner_total_first_visit: Optional[int] = Field(default=None, sa_column=Column(Integer), description="总首次拜访合作伙伴数")
    partner_total_multi_visit: Optional[int] = Field(default=None, sa_column=Column(Integer), description="总多次拜访合作伙伴数")
    

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

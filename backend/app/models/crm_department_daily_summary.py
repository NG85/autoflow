from typing import Optional
from datetime import datetime, date
from sqlmodel import Field, Column, DateTime, func, SQLModel, Integer, String, Date, Text


class CRMDepartmentDailySummary(SQLModel, table=True):
    """部门/公司日报汇总表（只读）"""
    
    model_config = {"from_attributes": True}
        
    __tablename__ = 'crm_department_daily_summary'

    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增序列）")
    unique_id: str = Field(sa_column=Column(String(255), nullable=False), description="唯一性ID（必填）")
    report_date: date = Field(sa_column=Column(Date, nullable=False), description="日期")
    summary_type: str = Field(sa_column=Column(String(50), nullable=False), description="汇总类型(department/company)")
    department_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="部门ID（仅当summary_type=department时）")
    department_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="部门名称（仅当summary_type=department时）")
    
    # Assessment statistics
    assessment_red_count: Optional[int] = Field(default=0, sa_column=Column(Integer, default=0), description="评估为red的次数")
    assessment_yellow_count: Optional[int] = Field(default=0, sa_column=Column(Integer, default=0), description="评估为yellow的次数")
    assessment_green_count: Optional[int] = Field(default=0, sa_column=Column(Integer, default=0), description="评估为green的次数")
    total_assessments: Optional[int] = Field(default=0, sa_column=Column(Integer, default=0), description="总评估数")
    
    # Visit statistics
    total_first_visit: Optional[int] = Field(default=0, sa_column=Column(Integer, default=0), description="总首次拜访数")
    total_multi_visit: Optional[int] = Field(default=0, sa_column=Column(Integer, default=0), description="总多次拜访数")
    
    # End customer assessment statistics - 总体
    end_customer_total_red_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户总计评估为red的次数"
    )
    end_customer_total_yellow_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户总计评估为yellow的次数"
    )
    end_customer_total_green_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户总计评估为green的次数"
    )
    end_customer_total_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户总计数量"
    )
    
    # End customer assessment statistics - 首次跟进
    end_customer_first_visit_red_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户首次跟进评估为red的次数"
    )
    end_customer_first_visit_yellow_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户首次跟进评估为yellow的次数"
    )
    end_customer_first_visit_green_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户首次跟进评估为green的次数"
    )
    end_customer_first_visit_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户首次跟进数量"
    )
    
    # End customer assessment statistics - 多次跟进
    end_customer_regular_visit_red_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户多次跟进评估为red的次数"
    )
    end_customer_regular_visit_yellow_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户多次跟进评估为yellow的次数"
    )
    end_customer_regular_visit_green_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户多次跟进评估为green的次数"
    )
    end_customer_regular_visit_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="最终客户多次跟进数量"
    )
    
    # Partner assessment statistics
    partner_total_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="合作伙伴总计数量"
    )
    partner_first_visit_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="合作伙伴首次跟进数量"
    )
    partner_regular_visit_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="合作伙伴多次跟进数量"
    )
    
    partner_red_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="合作伙伴评估为red的次数"
    )
    partner_yellow_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="合作伙伴评估为yellow的次数"
    )
    partner_green_count: Optional[int] = Field(
        default=0,
        sa_column=Column(Integer, default=0),
        description="合作伙伴评估为green的次数"
    )
    
    # Summary content
    summary_content: Optional[str] = Field(default=None, sa_column=Column(Text), description="汇总内容（中文）")
    summary_content_en: Optional[str] = Field(default=None, sa_column=Column(Text), description="汇总内容（英文）")
    summary_first_visit: Optional[str] = Field(default=None, sa_column=Column(Text), description="首次拜访汇总内容（中文）")
    summary_regular_visit: Optional[str] = Field(default=None, sa_column=Column(Text), description="多次拜访汇总内容（中文）")
    
    # Key highlights and concerns
    key_highlights: Optional[str] = Field(default=None, sa_column=Column(Text), description="关键亮点（JSON格式）")
    key_concerns: Optional[str] = Field(default=None, sa_column=Column(Text), description="关键关注点（JSON格式）")

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

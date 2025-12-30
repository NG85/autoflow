from typing import Optional
from datetime import date
from sqlmodel import Field, Column, String, Date, Text, Index

from app.models.base import UpdatableBaseModel
from app.models.base import UUIDBaseModel
from sqlalchemy import UniqueConstraint

class CRMWeeklyFollowupSummary(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    CRM 周跟进总结（公司 / 部门维度）

    - summary_type = "company": 公司级（department_name 为空）
    - summary_type = "department": 部门级（department_name 必填）
    """

    class Config:
        orm_mode = True

    __tablename__ = "crm_weekly_followup_summary"

    week_start: date = Field(sa_column=Column(Date, nullable=False), description="周开始日期（周日）")
    week_end: date = Field(sa_column=Column(Date, nullable=False), description="周结束日期（周六）")

    summary_type: str = Field(sa_column=Column(String(50), nullable=False), description="汇总类型(company/department)")
    # 为了保证唯一性约束在 TiDB/MySQL 上生效：company 行使用空字符串而不是 NULL
    department_id: str = Field(default="", sa_column=Column(String(100), nullable=False), description="部门ID（company时为空字符串）")
    department_name: str = Field(default="", sa_column=Column(String(255), nullable=False), description="部门名称（company时为空字符串）")
    title: str = Field(default="", sa_column=Column(String(255), nullable=False), description="周总结名称（用于列表展示）")

    summary_content: Optional[str] = Field(default=None, sa_column=Column(Text), description="汇总内容（中文）")

    __table_args__ = (
        UniqueConstraint(
            "week_start",
            "week_end",
            "summary_type",
            "department_name",
            name="ux_crm_weekly_followup_summary",
        ),
        Index("idx_weekly_followup_summary_week", "week_start", "week_end"),
        Index("idx_weekly_followup_summary_type_dept", "summary_type", "department_name"),
        Index("idx_weekly_followup_summary_dept_id", "department_id"),
    )



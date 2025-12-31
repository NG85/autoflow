from typing import Optional
from datetime import date

from sqlmodel import Field, Column, String, Date, Text, Index
from sqlalchemy import UniqueConstraint

from app.models.base import UpdatableBaseModel
from app.models.base import UUIDBaseModel
from sqlalchemy import JSON


class CRMWeeklyFollowupEntitySummary(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    CRM 周跟进实体明细（用于后台列表展示与单行评论）

    entity_type 优先级：opportunity -> account -> partner
    """

    class Config:
        orm_mode = True

    __tablename__ = "crm_weekly_followup_entity_summary"

    week_start: date = Field(sa_column=Column(Date, nullable=False), description="周开始日期（周日）")
    week_end: date = Field(sa_column=Column(Date, nullable=False), description="周结束日期（周六）")
    department_id: Optional[str] = Field(default=None, sa_column=Column(String(100), nullable=True), description="团队/部门ID")
    department_name: str = Field(sa_column=Column(String(255), nullable=False), description="团队/部门名称")

    entity_type: str = Field(sa_column=Column(String(50), nullable=False), description="实体类型(opportunity/account/partner)")
    entity_id: str = Field(sa_column=Column(String(255), nullable=False), description="实体ID（与 entity_type 对应）")

    account_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="客户ID")
    account_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="客户名称")
    opportunity_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="商机ID")
    opportunity_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="商机名称")
    partner_id: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="合作伙伴ID")
    partner_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="合作伙伴名称")

    owner_user_id: Optional[str] = Field(default=None, sa_column=Column(String(64)), description="负责销售用户ID（recorder_id）")
    owner_name: Optional[str] = Field(default=None, sa_column=Column(String(255)), description="负责销售姓名")

    progress: Optional[str] = Field(default=None, sa_column=Column(Text), description="本周进展")
    risks: Optional[str] = Field(default=None, sa_column=Column(Text), description="风险/问题")

    evidence_record_ids: Optional[str] = Field(default=None, sa_column=Column(Text), description="证据拜访记录ID列表（JSON数组字符串）")

    # 多人评论：JSON 数组，元素结构由上层 API 约束
    comments: Optional[list[dict]] = Field(default=None, sa_column=Column(JSON, nullable=True), description="评论列表（人工可编辑，JSON数组）")

    __table_args__ = (
        UniqueConstraint(
            "week_start",
            "week_end",
            "department_name",
            "entity_type",
            "entity_id",
            name="ux_crm_weekly_followup_entity",
        ),
        Index("idx_weekly_followup_entity_week", "week_start", "week_end"),
        Index("idx_weekly_followup_entity_dept_id", "department_id"),
        Index("idx_weekly_followup_entity_dept", "department_name"),
        Index("idx_weekly_followup_entity_entity", "entity_type", "entity_id"),
        Index("idx_weekly_followup_entity_owner", "owner_user_id"),
    )



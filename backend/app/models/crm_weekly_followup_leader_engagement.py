from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Date, DateTime, Field, Index, String

from app.models.base import UpdatableBaseModel, UUIDBaseModel


class CRMWeeklyFollowupLeaderEngagement(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    CRM 周跟进总结 - 团队负责人参与度（已阅/评论）记录。

    唯一键：summary_id + leader_user_id（幂等 upsert）。
    """

    class Config:
        orm_mode = True

    __tablename__ = "crm_weekly_followup_leader_engagement"

    summary_id: UUID = Field(index=True, nullable=False, description="部门周总结ID")

    # 反范式冗余：便于按周/部门快速聚合统计
    week_start: date = Field(sa_column=Column(Date, nullable=False), description="周开始日期（周日）")
    week_end: date = Field(sa_column=Column(Date, nullable=False), description="周结束日期（周六）")
    department_id: str = Field(
        default="",
        sa_column=Column(String(100), nullable=False),
        description="部门ID",
    )
    department_name: str = Field(
        default="",
        sa_column=Column(String(255), nullable=False),
        description="部门名称",
    )

    leader_user_id: str = Field(
        sa_column=Column(String(64), nullable=False),
        description="团队负责人 user_id（UUID字符串）",
    )

    reviewed_at: Optional[datetime] = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        description="已阅时间",
    )
    commented_at: Optional[datetime] = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        description="评论动作时间（该周该部门任一entity发生评论即置位）",
    )

    __table_args__ = (
        UniqueConstraint("summary_id", "leader_user_id", name="ux_weekly_followup_leader_engagement"),
        Index("idx_weekly_followup_leader_engagement_week_dept", "week_start", "week_end", "department_name"),
        Index("idx_weekly_followup_leader_engagement_week_dept_id", "week_start", "week_end", "department_id"),
        Index("idx_weekly_followup_leader_engagement_leader_reviewed", "leader_user_id", "reviewed_at"),
    )


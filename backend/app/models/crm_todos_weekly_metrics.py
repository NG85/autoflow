from __future__ import annotations

from datetime import date

from sqlalchemy import Column, Date, Integer, String, UniqueConstraint
from sqlmodel import Field

from app.models.base import UUIDBaseModel, UpdatableBaseModel


class CRMTodosWeeklyMetrics(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    销售任务周指标（按人粒度，便于后续按部门/公司聚合）

    说明：
    - 本表由本服务定时任务写入（幂等 upsert）
    - department_id 通过 user_department_relation 映射得到
    - data_source 为空字符串表示“不适用”（如 cancelled/no_due_date）
    - data_source="__ALL__" 表示该 metric 的总计（跨所有 data_source 的合计）
    """

    __tablename__ = "crm_todos_weekly_metrics"

    __table_args__ = (
        UniqueConstraint(
            "week_start",
            "week_end",
            "assignee_id",
            "department_id",
            "metric",
            "data_source",
            name="ux_crm_todos_weekly_metrics",
        ),
    )

    week_start: date = Field(sa_column=Column(Date, nullable=False, index=True))
    week_end: date = Field(sa_column=Column(Date, nullable=False, index=True))

    # 负责人标识：优先写 user_id(UUID 字符串)；解析不到则写 owner_id 原值（保持可追溯）
    assignee_id: str = Field(sa_column=Column(String(100), nullable=False, index=True))

    department_id: str = Field(sa_column=Column(String(100), nullable=False, index=True))

    # metric: completed/overdue/next_week/cancelled/no_due_date ...
    metric: str = Field(sa_column=Column(String(50), nullable=False, index=True))

    # data_source: TodoDataSourceType 值或 "__ALL__"/""（见类注释）
    data_source: str = Field(default="", sa_column=Column(String(100), nullable=False, index=True))

    value: int = Field(default=0, sa_column=Column(Integer, nullable=False))

    class Config:
        orm_mode = True


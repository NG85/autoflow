from __future__ import annotations

from datetime import date

from sqlalchemy import Column, Date, Integer, String, UniqueConstraint
from sqlmodel import Field

from app.models.base import UUIDBaseModel, UpdatableBaseModel


class CRMTodoMetricsFacts(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    CRM 销售任务指标事实表（EAV/列式模拟）

    维度：
    - anchor: stock（存量快照）/ due_week（按 due_date 落周）等
    - grain: week / day / hour
    - period_start/period_end: 周或日的区间（hour 粒度也用 date 表示“当天”）
    - hour_of_day: 0..23（仅 grain=hour 使用；其他为 0）
    - subject_type: company / assignee
    - subject_id: "__ALL__" 或 assignee_id
    - data_source: MANUAL / AI_EXTRACTION / PIPELINE_PLAYBOOK / "__ALL__" / ""

    指标：
    - metric: no_due_date_stock_total / status_pending ...
    - value_int: 指标值（整型）
    """

    __tablename__ = "crm_todo_metrics_facts"

    __table_args__ = (
        UniqueConstraint(
            "anchor",
            "grain",
            "period_start",
            "period_end",
            "hour_of_day",
            "subject_type",
            "subject_id",
            "data_source",
            "metric",
            name="ux_crm_todo_metrics_facts",
        ),
    )

    anchor: str = Field(sa_column=Column(String(20), nullable=False, index=True))
    grain: str = Field(sa_column=Column(String(10), nullable=False, index=True))

    period_start: date = Field(sa_column=Column(Date, nullable=False, index=True))
    period_end: date = Field(sa_column=Column(Date, nullable=False, index=True))

    hour_of_day: int = Field(default=0, sa_column=Column(Integer, nullable=False, index=True))

    subject_type: str = Field(sa_column=Column(String(20), nullable=False, index=True))
    subject_id: str = Field(sa_column=Column(String(100), nullable=False, index=True))

    data_source: str = Field(default="", sa_column=Column(String(100), nullable=False, index=True))

    metric: str = Field(sa_column=Column(String(50), nullable=False, index=True))

    value_int: int = Field(default=0, sa_column=Column(Integer, nullable=False))

    class Config:
        orm_mode = True


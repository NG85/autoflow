from __future__ import annotations

from datetime import date

from sqlalchemy import Column, Date, Integer, String, UniqueConstraint
from sqlmodel import Field

from app.models.base import UUIDBaseModel, UpdatableBaseModel


class CRMVisitMetricsFacts(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    CRM 拜访指标事实表（EAV/列式模拟）

    维度：
    - anchor: entry（按 last_modified_time 折算北京日期）/ followup（按 visit_communication_date）
    - grain: week / day
    - period_start/period_end: 周或日的区间
    - subject_type: sales / department / company
    - subject_id: recorder_id(32hex) / department_id / "__ALL__"
    - department_id/name: 快照字段（sales 维度必填；department/company 可冗余以便下游直查）
    - weekday_iso: 1..7（Mon..Sun）；非“星期分布”类指标用 0

    指标：
    - metric: total_visits / call_high_visits ...
    - value_int: 指标值（整型）
    """

    __tablename__ = "crm_visit_metrics_facts"

    __table_args__ = (
        UniqueConstraint(
            "anchor",
            "grain",
            "period_start",
            "period_end",
            "subject_type",
            "subject_id",
            "department_id",
            "metric",
            "weekday_iso",
            name="ux_crm_visit_metrics_facts",
        ),
    )

    anchor: str = Field(sa_column=Column(String(20), nullable=False, index=True))  # entry/followup
    grain: str = Field(sa_column=Column(String(10), nullable=False, index=True))  # week/day

    period_start: date = Field(sa_column=Column(Date, nullable=False, index=True))
    period_end: date = Field(sa_column=Column(Date, nullable=False, index=True))

    subject_type: str = Field(sa_column=Column(String(20), nullable=False, index=True))  # sales/department/company
    subject_id: str = Field(sa_column=Column(String(100), nullable=False, index=True))

    department_id: str = Field(default="", sa_column=Column(String(100), nullable=False, index=True))
    department_name: str = Field(default="", sa_column=Column(String(255), nullable=False, index=True))

    metric: str = Field(sa_column=Column(String(50), nullable=False, index=True))
    weekday_iso: int = Field(default=0, sa_column=Column(Integer, nullable=False, index=True))

    value_int: int = Field(default=0, sa_column=Column(Integer, nullable=False))

    model_config = {"from_attributes": True}


from typing import Optional

from sqlalchemy import Column, Index, Integer, String, Text, UniqueConstraint
from sqlmodel import Field

from app.models.base import UUIDBaseModel, UpdatableBaseModel


class FeishuBillingUsageReport(UUIDBaseModel, UpdatableBaseModel, table=True):
    """
    飞书计费 pending_insert 调用结果落库，供对账与失败补偿排查。
    trace_id 与计费侧幂等键一致，同一 trace_id 重复上报时更新本行最终状态。
    """

    model_config = {"from_attributes": True}

    __tablename__ = "feishu_billing_usage_report"

    trace_id: str = Field(
        sa_column=Column(String(220), nullable=False),
        description="与计费接口 trace_id 一致",
    )
    ai_module_key: str = Field(
        sa_column=Column(String(128), nullable=False),
        description="计费模块键",
    )
    operator: str = Field(
        default="system",
        sa_column=Column(String(128), nullable=False),
        description="operator 字段原值",
    )
    review_detail: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="review_detail 原文",
    )
    status: str = Field(
        sa_column=Column(String(20), nullable=False),
        description="success 或 failed",
    )
    last_api_code: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
        description="响应 JSON 中 code；异常未拿到 JSON 时为 NULL",
    )
    last_message: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="响应 msg 或异常信息摘要",
    )
    attempts: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False),
        description="本轮调用内累计尝试次数（含最后一次）",
    )
    last_exception_type: Optional[str] = Field(
        default=None,
        sa_column=Column(String(200), nullable=True),
        description="最后一次 transport 异常类型名",
    )

    __table_args__ = (
        UniqueConstraint("trace_id", name="ux_feishu_billing_usage_report_trace"),
        Index("idx_feishu_billing_usage_status_created", "status", "created_at"),
        Index("idx_feishu_billing_usage_module_created", "ai_module_key", "created_at"),
    )

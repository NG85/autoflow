from typing import Optional
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Column, DateTime, SQLModel, Text, String, Integer, func, Index


class CRMForecastTypeMapping(SQLModel, table=True):
    """Forecast type 标准映射表"""

    model_config = {"from_attributes": True}

    __tablename__ = "crm_forecast_type_mappings"

    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID（自增）")
    internal_type: str = Field(
        sa_column=Column(String(50), nullable=False),
        description="内部类型键 (e.g. commit, closed_won, upside, pipeline, lost_cancel)",
    )
    customer_values: str = Field(
        sa_column=Column(Text, nullable=False),
        description="客户侧文本值列表（JSON数组，不区分大小写匹配）",
    )
    display_order: int = Field(
        sa_column=Column(Integer, nullable=False),
        description="前端展示顺序（数字越小越靠前）",
    )
    is_active: bool = Field(default=True, description="是否启用")
    description: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="配置描述",
    )
    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
        description="创建时间",
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
        description="更新时间",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=False, server_default=func.now()),
        description="创建时间",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
        description="更新时间",
    )

    __table_args__ = (
        UniqueConstraint("internal_type", name="idx_crm_ftm_internal_type"),
        Index("idx_crm_ftm_display_order", "display_order"),
        Index("idx_crm_ftm_is_active", "is_active"),
    )

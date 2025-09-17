from typing import Optional
from datetime import datetime
from sqlmodel import Field, Column, DateTime, SQLModel, func


class CRMSystemConfiguration(SQLModel, table=True):
    """CRM系统配置表"""
    
    id: Optional[int] = Field(default=None, primary_key=True)
    config_type: str = Field(max_length=255, description="配置类型（如CommunicationCategory/VisitStatus/UserRoles等）")
    config_key: str = Field(max_length=255, description="配置键")
    config_value: str = Field(max_length=255, description="配置值")
    is_active: bool = Field(default=True, description="是否启用")
    description: Optional[str] = Field(default=None, description="配置描述")
    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
        description="创建时间"
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
        description="更新时间"
    )

    __tablename__ = "crm_system_configurations"


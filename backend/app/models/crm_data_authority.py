"""
CRM数据权限相关模型
"""

from datetime import datetime
from typing import Optional

from sqlmodel import DateTime, Field, SQLModel, func


class CrmDataAuthority(SQLModel, table=True):
    """
    CRM数据权限表

    数据由其他系统维护，本服务仅查询使用。
    """
    __tablename__ = "crm_data_authority"
    
    # 主键ID（自增序列）
    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        index=True,
        sa_column_kwargs={"autoincrement": True}
    )
    
    # 数据权限相关字段
    # 注意：业务上应使用 crm_id 做权限关联；user_id 在该表中可能无法直接使用。
    crm_id: str = Field(max_length=255, index=True, description="CRM用户id")
    type: str = Field(max_length=255, index=True, description="数据类型")
    data_id: str = Field(max_length=255, index=True, description="数据id")

    user_id: Optional[str] = Field(default=None, max_length=255, description="用户id（可能不稳定，不建议用于鉴权）")
    
    # 删除标识
    delete_flag: Optional[bool] = Field(default=False, description="删除标识, True已删除")

    create_time: Optional[datetime] = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()},
    )
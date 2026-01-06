from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class UserDepartmentRelation(SQLModel, table=True):
    """
    用户-部门关系表（只读）

    数据由其他系统维护，本服务仅查询使用。
    """

    __tablename__ = "user_department_relation"

    id: Optional[int] = Field(default=None, primary_key=True)

    create_time: Optional[datetime] = Field(default=None, nullable=True)
    update_time: Optional[datetime] = Field(default=None, nullable=True)

    # 外部系统用户ID（通常是 UUID 字符串）
    user_id: Optional[str] = Field(default=None, max_length=36, index=True, nullable=True)

    # CRM 用户ID（owner_id 可能使用该值）
    crm_user_id: str = Field(max_length=100, index=True)

    department_id: str = Field(max_length=100, index=True)

    is_primary: bool = Field(default=False, index=True)
    is_leader: bool = Field(default=False, index=True)

    title: Optional[str] = Field(default=None, max_length=100)
    user_name: Optional[str] = Field(default=None, max_length=100)
    
    is_active: bool = Field(default=True, index=True)

    class Config:
        orm_mode = True


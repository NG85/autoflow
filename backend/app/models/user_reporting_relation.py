from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

class UserReportingRelation(SQLModel, table=True):
    """用户汇报关系表（只读）

    数据由其他系统维护，本服务仅查询使用。
    """
    
    __tablename__ = "user_reporting_relation"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # 上级用户ID（CRM用户ID）
    from_user_id: str = Field(max_length=100, index=True)
    
    # 下级用户ID（CRM用户ID）
    to_user_id: str = Field(max_length=100, index=True)
    
    # 层级（1表示直接关系，2表示间接关系，以此类推）
    level: int = Field(default=1, index=True)
    
    # 是否有效
    is_active: bool = Field(default=True, index=True)
    
    create_time: Optional[datetime] = Field(default=None, nullable=True)
    update_time: Optional[datetime] = Field(default=None, nullable=True)
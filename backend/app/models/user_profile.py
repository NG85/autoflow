from typing import Optional
from uuid import UUID
from datetime import datetime
from sqlmodel import (
    Field,
    SQLModel,
    DateTime,
    func,
    Relationship as SQLRelationship,
)

from app.models.base import UpdatableBaseModel


class UserProfile(UpdatableBaseModel, table=True):
    """用户档案表 - 存储用户的核心组织架构信息"""
    
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # 关联字段 - 支持多种用户来源
    user_id: Optional[UUID] = Field(foreign_key="users.id", nullable=True, description="关联系统用户表")
    oauth_user_id: Optional[str] = Field(max_length=255, nullable=True, description="关联OAuth用户表的ask_id")
    
    # 飞书相关字段
    feishu_open_id: Optional[str] = Field(max_length=255, nullable=True, description="飞书open_id，用于消息推送")
    
    # 核心组织架构信息
    name: Optional[str] = Field(max_length=255, nullable=True, description="姓名")
    department: Optional[str] = Field(max_length=255, nullable=True, description="所属部门")
    position: Optional[str] = Field(max_length=255, nullable=True, description="职位/岗位")
    
    # 直属上级信息
    direct_manager_id: Optional[str] = Field(max_length=255, nullable=True, description="直属上级ID")
    direct_manager_name: Optional[str] = Field(max_length=255, nullable=True, description="直属上级姓名")
    
    # 状态信息
    is_active: bool = Field(default=True, description="档案是否有效")
    
    # 关联关系
    user: Optional["User"] = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "UserProfile.user_id == User.id",
        },
    )
    
    __tablename__ = "user_profiles"
    
    class Config:
        orm_mode = True

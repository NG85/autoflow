from __future__ import annotations

from typing import Optional, Dict, Any
from datetime import datetime

from sqlalchemy import Column, JSON
from sqlmodel import SQLModel, Field


class OAuthUser(SQLModel, table=True):
    """
    只读模型：外部OAuth服务维护的用户表 `user`
    注意：此模型用于查询用途，不参与本项目的写操作与迁移。
    """

    __tablename__ = "user"

    # 主键
    id: Optional[int] = Field(default=None, primary_key=True, description="db主键id")

    # 基础字段
    uid: Optional[str] = Field(default=None, description="为用户生成的uid")
    user_id: Optional[str] = Field(default=None, description="用户自定义/企业飞书内id")
    role: Optional[str] = Field(default=None, description="用户角色")
    name: Optional[str] = Field(default=None, description="用户姓名")
    en_name: Optional[str] = Field(default=None, description="用户英文名")
    avatar_url: Optional[str] = Field(default=None, description="用户头像URL")
    email: Optional[str] = Field(default=None, description="用户邮箱")
    mobile: Optional[str] = Field(default=None, description="用户手机号")
    password: Optional[str] = Field(default=None, description="用户密码")
    email_verified: Optional[datetime] = Field(default=None, description="邮箱验证时间")
    corporation_id: Optional[str] = Field(default=None, description="企业ID")
    open_id: Optional[str] = Field(default=None, description="开放ID")
    union_id: Optional[str] = Field(default=None, description="统一ID")
    enterprise_email: Optional[str] = Field(default=None, description="企业邮箱")
    tenant_key: Optional[str] = Field(default=None, description="租户密钥")
    employee_no: Optional[str] = Field(default=None, description="员工编号")

    # 审计与版本
    creator: Optional[str] = Field(default=None, description="创建者")
    create_time: Optional[datetime] = Field(default=None, description="创建时间")
    updater: Optional[str] = Field(default=None, description="更新者")
    update_time: Optional[datetime] = Field(default=None, description="最后更新时间")
    version: Optional[int] = Field(default=1, description="数据版本")
    delete_flag: Optional[int] = Field(default=0, description="删除标识, 1已删除")

    # 其他
    channel: Optional[str] = Field(default=None, description="登录渠道")
    ask_user_id: Optional[str] = Field(default=None, description="ask服务的email")
    ask_id: Optional[str] = Field(default=None, description="ask服务的id")
    fxiaoke_id: Optional[str] = Field(default=None, description="fxiaoke用户id")
    extra_info: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="扩展信息",
    )



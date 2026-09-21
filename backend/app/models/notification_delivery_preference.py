"""用户推送偏好：有资格的人默认接收，receive=false 表示关掉。"""

from typing import Optional
from uuid import UUID

from sqlalchemy import UniqueConstraint, Column, String, Boolean
from sqlmodel import Field, SQLModel

from app.models.base import UpdatableBaseModel, UUIDBaseModel


class NotificationDeliveryPreference(UUIDBaseModel, UpdatableBaseModel, table=True):
    __tablename__ = "notification_delivery_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "scene",
            "variant",
            "department_id",
            name="ux_notification_delivery_pref",
        ),
    )

    user_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)
    scene: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    # 空字符串表示该 scene 下全部变体
    variant: str = Field(default="", sa_column=Column(String(64), nullable=False))
    # 公司级为空字符串；部门级为 department_mirror.unique_id
    department_id: str = Field(default="", sa_column=Column(String(100), nullable=False))
    receive: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False),
        description="false=有资格但关掉推送",
    )

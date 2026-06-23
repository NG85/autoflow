"""拜访记录等事件的抄送规则配置。"""

from typing import Optional, List
from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship as SQLRelationship, SQLModel

from app.models.base import UpdatableBaseModel


EVENT_TYPE_VISIT_RECORD_CARD = "visit_record_card"
SCOPE_TYPE_USER = "user"
SCOPE_TYPE_GLOBAL = "global"


class NotificationCcRule(UpdatableBaseModel, SQLModel, table=True):
    __tablename__ = "notification_cc_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(max_length=64, index=True, description="事件类型，如 visit_record_card")
    scope_type: str = Field(max_length=32, index=True, description="匹配维度：user / global")
    scope_user_id: Optional[UUID] = Field(
        default=None,
        foreign_key="users.id",
        index=True,
        description="scope_type=user 时：录入人 users.id",
    )
    scope_department_id: Optional[str] = Field(
        default=None,
        max_length=255,
        description="预留：部门维度",
    )
    priority: int = Field(default=0, description="管理端排序与日志，不参与过滤")
    enabled: bool = Field(default=True, index=True)
    description: Optional[str] = Field(default=None, max_length=512)

    recipients: List["NotificationCcRuleRecipient"] = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "selectin",
            "primaryjoin": "NotificationCcRule.id == foreign(NotificationCcRuleRecipient.rule_id)",
        },
    )


class NotificationCcRuleRecipient(UpdatableBaseModel, SQLModel, table=True):
    __tablename__ = "notification_cc_rule_recipients"
    __table_args__ = (
        UniqueConstraint("rule_id", "user_id", name="uq_notification_cc_rule_recipient"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_id: int = Field(foreign_key="notification_cc_rules.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)

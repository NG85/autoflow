import enum
from uuid import UUID
from typing import Optional, Dict
from pydantic import BaseModel
from datetime import datetime

from sqlmodel import (
    Field,
    Column,
    DateTime,
    JSON,
    String,
    Relationship as SQLRelationship,
)

from .base import IntEnumType, UUIDBaseModel, UpdatableBaseModel


class ChatType(str, enum.Enum):
    DEFAULT = "default"
    CLIENT_VISIT_GUIDE = "client_visit_guide"
    REVIEW_SESSION = "review_session"


class ChatMode(str, enum.Enum):
    CREATE_CVG_REPORT = "create_cvg_report"
    SAVE_CVG_REPORT = "save_cvg_report"
    # cvg_chat 与 chat_type 组合决定计费，见 chat._should_bill_sia：
    # - default + cvg_chat：三方拜访攻略流程内 chat，不计 SIA
    # - client_visit_guide + cvg_chat：基于已有攻略报告问答，计 SIA
    CVG_CHAT = "cvg_chat"
    DEFAULT = "default"


class ChatVisibility(int, enum.Enum):
    PRIVATE = 0
    PUBLIC = 1


class Chat(UUIDBaseModel, UpdatableBaseModel, table=True):
    title: str = Field(max_length=256)
    engine_id: int = Field(foreign_key="chat_engines.id", nullable=True)
    engine: "ChatEngine" = SQLRelationship(  # noqa:F821
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "Chat.engine_id == ChatEngine.id",
        },
    )
    # FIXME: why fastapi_pagination return string(json) instead of dict?
    engine_options: Dict | str = Field(default={}, sa_column=Column(JSON))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    user_id: UUID = Field(foreign_key="users.id", nullable=True)
    user: "User" = SQLRelationship(  # noqa:F821
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "Chat.user_id == User.id",
        },
    )
    browser_id: str = Field(max_length=50, nullable=True)
    origin: str = Field(max_length=256, default=None, nullable=True)
    visibility: ChatVisibility = Field(
        sa_column=Column(
            IntEnumType(ChatVisibility),
            nullable=False,
            default=ChatVisibility.PRIVATE,
        )
    )
    chat_type: ChatType = Field(
        sa_column=Column(
            IntEnumType(ChatType),
            nullable=False,
            default=ChatType.DEFAULT,
        )
    )
    # DB 存 VARCHAR；合法取值由应用层 ChatMode 约束，新增 mode 无需改表
    chat_mode: ChatMode = Field(
        default=ChatMode.DEFAULT,
        sa_column=Column(
            String(50),
            nullable=False,
            server_default=ChatMode.DEFAULT.value,
        ),
    )

    __tablename__ = "chats"


class ChatItem(BaseModel):
    """Chat list item enriched with the owner's display name (from user_profiles)."""

    id: UUID
    title: str
    engine_id: Optional[int] = None
    engine_options: Dict | str = {}
    deleted_at: Optional[datetime] = None
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None
    browser_id: Optional[str] = None
    origin: Optional[str] = None
    visibility: ChatVisibility
    chat_type: ChatType
    chat_mode: ChatMode = ChatMode.DEFAULT
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ChatUpdate(BaseModel):
    title: Optional[str] = None
    visibility: Optional[ChatVisibility] = None


class ChatFilters(BaseModel):
    created_at_start: Optional[datetime] = None
    created_at_end: Optional[datetime] = None
    updated_at_start: Optional[datetime] = None
    updated_at_end: Optional[datetime] = None
    chat_origin: Optional[str] = None
    # user_id: Optional[UUID] = None          # no use now
    engine_id: Optional[int] = None
    chat_type: Optional[ChatType] = None
    chat_mode: Optional[ChatMode] = None
    exclude_chat_mode: Optional[ChatMode] = None


class ChatOrigin(BaseModel):
    origin: str
    chats: int

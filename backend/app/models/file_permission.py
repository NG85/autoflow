import enum

from sqlalchemy import Column, text
from app.models.base import IntEnumType
from sqlmodel import SQLModel, Field, Relationship as SQLRelationship
from typing import Optional
from datetime import UTC, datetime
from uuid import UUID
from app.models.document import DocumentCategory

class PermissionType(str, enum.Enum):
    READ = "read"  # 只读权限，用户可以查看文件内容
    OWNER = "owner"  # 所有者权限，用户可以完全控制文件，包括修改和删除

class FilePermission(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True, description="主键ID")
    
    file_id: int = Field(
        foreign_key="uploads.id",
        nullable=False,
        description="文件ID，关联到uploads表"
    )
    file: "Upload" = SQLRelationship(  # noqa:F821
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "FilePermission.file_id == Upload.id",
        },
    )
    
    user_id: Optional[UUID] = Field(
        foreign_key="users.id",
        nullable=True,
        description="用户ID，关联到users表，为空表示公开权限"
    )
    user: Optional["User"] = SQLRelationship(  # noqa:F821
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "FilePermission.user_id == User.id",
        },
    )

    permission_type: PermissionType = Field(
        sa_column=Column(
            IntEnumType(PermissionType),
            nullable=False,
            default=PermissionType.READ,
        ),
        description="权限类型：read-只读权限，owner-所有者权限"
    )
    
    granted_by: UUID = Field(
        foreign_key="users.id",
        nullable=False,
        description="授权人用户ID，关联到users表"
    )
    
    granted_at: datetime = Field(
        sa_column=Column(
            "granted_at",
            nullable=False,
            server_default=text("now()"),
        ),
        description="授权时间"
    )
    
    expires_at: Optional[datetime] = Field(
        default=None,
        description="权限过期时间，为空表示永久有效"
    )

    __tablename__ = "file_permissions"

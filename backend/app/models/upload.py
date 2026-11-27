from uuid import UUID
from typing import Optional

from sqlalchemy import JSON, Computed, Index
from sqlmodel import (
    Field,
    Column,
    String,
    Relationship as SQLRelationship,
)

from app.core.config import DatabaseDialect, settings
from app.models.base import UpdatableBaseModel
from app.types import MimeTypes
from app.models.document import DocumentMetadata

if settings.DATABASE_DIALECT == DatabaseDialect.POSTGRESQL:
    # PostgreSQL 16 only stores generated columns.
    _CATEGORY_SQL = "(meta->>'category')"
    _CATEGORY_PERSISTED = True
else:
    _CATEGORY_SQL = "(JSON_UNQUOTE(meta->'$.category'))"
    _CATEGORY_PERSISTED = False

class Upload(UpdatableBaseModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    size: int = Field(default=0)
    path: str = Field(max_length=255)
    mime_type: MimeTypes = Field(sa_column=Column(String(128), nullable=False))
    user_id: UUID = Field(foreign_key="users.id", nullable=True)
    user: "User" = SQLRelationship(  # noqa:F821
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "Upload.user_id == User.id",
        },
    )
    meta: dict = Field(default={}, sa_column=Column(JSON))
    # Derived from meta so file-permission filters can compare Upload.category.
    category: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(100),
            Computed(_CATEGORY_SQL, persisted=_CATEGORY_PERSISTED),
        ),
    )

    __tablename__ = "uploads"
    __table_args__ = (Index("ix_uploads_category", "category"),)

    def set_metadata(self, metadata: DocumentMetadata):
        self.meta = metadata.model_dump()

    def get_metadata(self) -> DocumentMetadata | None:
        if isinstance(self.meta, dict):
            return DocumentMetadata(**self.meta)
        return DocumentMetadata()
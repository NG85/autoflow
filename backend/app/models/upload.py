from uuid import UUID
from typing import Optional

from sqlalchemy import JSON
from sqlmodel import (
    Field,
    Column,
    String,
    Relationship as SQLRelationship,
)

from app.models.base import UpdatableBaseModel
from app.types import MimeTypes
from app.models.document import DocumentMetadata

class Upload(UpdatableBaseModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255)
    size: int = Field(default=0)
    path: str = Field(max_length=255)
    mime_type: MimeTypes = Field(sa_column=Column(String(128), nullable=False))
    user_id: UUID = Field(foreign_key="users.id", nullable=True)
    user: "User" = SQLRelationship(
        sa_relationship_kwargs={
            "lazy": "joined",
            "primaryjoin": "Upload.user_id == User.id",
        },
    )
    meta: dict = Field(default={}, sa_column=Column(JSON))

    __tablename__ = "uploads"

    def set_metadata(self, metadata: DocumentMetadata):
        self.meta = metadata.model_dump()

    def get_metadata(self) -> DocumentMetadata | None:
        if isinstance(self.meta, dict):
            return DocumentMetadata(**self.meta)
        return None
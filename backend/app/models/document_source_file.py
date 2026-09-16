from typing import Optional
from uuid import UUID

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import UpdatableBaseModel


class DocumentSourceFile(UpdatableBaseModel, SQLModel, table=True):
    """Confirmed mapping from a knowledge-base document to its original upload."""

    __tablename__ = "document_source_files"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_document_source_files_document_id"),
        UniqueConstraint(
            "original_upload_id", name="uq_document_source_files_original_upload_id"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", nullable=False)
    original_upload_id: int = Field(foreign_key="uploads.id", nullable=False)
    confirmed_by: UUID = Field(foreign_key="users.id", nullable=False)

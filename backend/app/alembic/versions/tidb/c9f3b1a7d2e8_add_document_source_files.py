"""add document_source_files table

Revision ID: c9f3b1a7d2e8
Revises: b8c4d1e2f3a5
Create Date: 2026-09-16 11:40:00.000000

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "c9f3b1a7d2e8"
down_revision = "b8c4d1e2f3a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_source_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("original_upload_id", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["original_upload_id"], ["uploads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uq_document_source_files_document_id"),
        sa.UniqueConstraint(
            "original_upload_id", name="uq_document_source_files_original_upload_id"
        ),
    )
    op.create_index(op.f("ix_documents_name"), "documents", ["name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_name"), table_name="documents")
    op.drop_table("document_source_files")

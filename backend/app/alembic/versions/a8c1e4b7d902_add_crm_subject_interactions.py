"""add subject comments and task links

Revision ID: a8c1e4b7d902
Revises: e5f9a2b3c4d6
Create Date: 2026-10-13 14:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a8c1e4b7d902"
down_revision = "e5f9a2b3c4d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_subject_interactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("scene", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("reply_to_id", sa.String(length=36), nullable=True),
        sa.Column("author_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("author", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("todo_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_type",
            "subject_id",
            "todo_id",
            name="ux_crm_subject_interactions_todo",
        ),
    )
    op.create_index(
        "ix_crm_subject_interactions_subject",
        "crm_subject_interactions",
        ["subject_type", "subject_id", "created_at"],
    )
    op.create_index(
        "ix_crm_subject_interactions_author",
        "crm_subject_interactions",
        ["kind", "author_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_subject_interactions_author", table_name="crm_subject_interactions")
    op.drop_index("ix_crm_subject_interactions_subject", table_name="crm_subject_interactions")
    op.drop_table("crm_subject_interactions")

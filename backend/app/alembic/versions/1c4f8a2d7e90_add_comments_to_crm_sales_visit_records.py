"""add_comments_to_crm_sales_visit_records

Revision ID: 1c4f8a2d7e90
Revises: 5c2a9a1b7d1a
Create Date: 2026-01-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1c4f8a2d7e90"
down_revision = "5c2a9a1b7d1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("comments", sa.JSON(), nullable=True, comment="评论列表（人工可编辑，JSON数组）"),
    )


def downgrade() -> None:
    op.drop_column("crm_sales_visit_records", "comments")



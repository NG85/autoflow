"""add card_push_status to crm_sales_visit_records

Revision ID: e7a3c1d92b40
Revises: c4e8b1a2f3d0
Create Date: 2026-05-15 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "e7a3c1d92b40"
down_revision = "c4e8b1a2f3d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("card_push_status", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "idx_card_push_status",
        "crm_sales_visit_records",
        ["card_push_status"],
    )


def downgrade() -> None:
    op.drop_index("idx_card_push_status", table_name="crm_sales_visit_records")
    op.drop_column("crm_sales_visit_records", "card_push_status")

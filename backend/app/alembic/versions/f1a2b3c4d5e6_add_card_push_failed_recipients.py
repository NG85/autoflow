"""add card_push_failed_recipients to crm_sales_visit_records

Revision ID: f1a2b3c4d5e6
Revises: a9f2e8c1d4b7
Create Date: 2026-07-02 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "a9f2e8c1d4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("card_push_failed_recipients", sa.JSON(), nullable=True),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("card_push_total_recipients", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crm_sales_visit_records", "card_push_total_recipients")
    op.drop_column("crm_sales_visit_records", "card_push_failed_recipients")

"""add external collaboration partner fields to crm_sales_visit_records

Revision ID: f8a2c6e1b904
Revises: e7a3c1d92b40
Create Date: 2026-06-01 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "f8a2c6e1b904"
down_revision = "e7a3c1d92b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("external_collaboration_partner_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("external_collaboration_partner_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crm_sales_visit_records", "external_collaboration_partner_id")
    op.drop_column("crm_sales_visit_records", "external_collaboration_partner_name")

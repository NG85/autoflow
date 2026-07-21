"""add followup_object fields to crm_sales_visit_records

Revision ID: c1d2e3f4a5b6
Revises: f2a8c3d1e904
Create Date: 2026-07-10 14:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "f2a8c3d1e904"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("followup_object_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("followup_object_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("followup_object_name", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "idx_followup_object_type_id",
        "crm_sales_visit_records",
        ["followup_object_type", "followup_object_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_followup_object_type_id", table_name="crm_sales_visit_records")
    op.drop_column("crm_sales_visit_records", "followup_object_name")
    op.drop_column("crm_sales_visit_records", "followup_object_id")
    op.drop_column("crm_sales_visit_records", "followup_object_type")

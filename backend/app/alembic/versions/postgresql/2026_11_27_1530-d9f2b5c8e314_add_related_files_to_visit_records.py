"""add related_files to crm_sales_visit_records

Revision ID: d9f2b5c8e314
Revises: b7e4a91c2d10
Create Date: 2026-11-27 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d9f2b5c8e314"
down_revision = "b7e4a91c2d10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("related_files", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("crm_sales_visit_records", "related_files")

"""add related_files to crm_sales_visit_records

Revision ID: c8e1a4f7b203
Revises: a8c1e4b7d902
Create Date: 2026-11-27 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c8e1a4f7b203"
down_revision = "a8c1e4b7d902"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "crm_sales_visit_records",
        sa.Column(
            "related_files",
            sa.JSON(),
            nullable=True,
            comment="相关文件资料（JSON数组，元素含 name、url）",
        ),
    )


def downgrade():
    op.drop_column("crm_sales_visit_records", "related_files")

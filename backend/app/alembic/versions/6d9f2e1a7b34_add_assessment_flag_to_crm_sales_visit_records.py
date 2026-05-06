"""add_assessment_flag_to_crm_sales_visit_records

Revision ID: 6d9f2e1a7b34
Revises: a1b2c3d4e5f6
Create Date: 2026-05-06 15:24:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6d9f2e1a7b34"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("assessment_flag", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crm_sales_visit_records", "assessment_flag")

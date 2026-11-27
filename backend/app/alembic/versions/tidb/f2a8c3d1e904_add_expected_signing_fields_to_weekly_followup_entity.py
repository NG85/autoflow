"""add forecast_amount and expected_closing_date to weekly followup entity summary

Revision ID: f2a8c3d1e904
Revises: a7c3e9f1b204
Create Date: 2026-07-10

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2a8c3d1e904"
down_revision = "a7c3e9f1b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_weekly_followup_entity_summary",
        sa.Column("forecast_amount", sa.Numeric(18, 2), nullable=True),
    )
    op.add_column(
        "crm_weekly_followup_entity_summary",
        sa.Column("expected_closing_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "idx_weekly_followup_entity_closing_date",
        "crm_weekly_followup_entity_summary",
        ["expected_closing_date"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_entity_forecast_amount",
        "crm_weekly_followup_entity_summary",
        ["forecast_amount"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_weekly_followup_entity_forecast_amount", table_name="crm_weekly_followup_entity_summary")
    op.drop_index("idx_weekly_followup_entity_closing_date", table_name="crm_weekly_followup_entity_summary")
    op.drop_column("crm_weekly_followup_entity_summary", "expected_closing_date")
    op.drop_column("crm_weekly_followup_entity_summary", "forecast_amount")

"""add followup_object fields to weekly followup entity summary

Revision ID: e6f7a8b9c0d1
Revises: c1d2e3f4a5b6
Create Date: 2026-07-14

"""

from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_weekly_followup_entity_summary",
        sa.Column("followup_object_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "crm_weekly_followup_entity_summary",
        sa.Column("followup_object_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "crm_weekly_followup_entity_summary",
        sa.Column("followup_object_name", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "idx_weekly_followup_entity_followup_object",
        "crm_weekly_followup_entity_summary",
        ["followup_object_type", "followup_object_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_weekly_followup_entity_followup_object",
        table_name="crm_weekly_followup_entity_summary",
    )
    op.drop_column("crm_weekly_followup_entity_summary", "followup_object_name")
    op.drop_column("crm_weekly_followup_entity_summary", "followup_object_id")
    op.drop_column("crm_weekly_followup_entity_summary", "followup_object_type")

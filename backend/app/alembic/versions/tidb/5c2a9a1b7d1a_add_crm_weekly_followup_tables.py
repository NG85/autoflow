"""add crm weekly followup tables

Revision ID: 5c2a9a1b7d1a
Revises: e1c3a9b4d2f0
Create Date: 2025-12-30

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = "5c2a9a1b7d1a"
down_revision = "e1c3a9b4d2f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_weekly_followup_summary",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("summary_type", sa.String(length=50), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("department_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("summary_content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_start",
            "week_end",
            "summary_type",
            "department_name",
            name="ux_crm_weekly_followup_summary",
        ),
    )
    op.create_index(
        "idx_weekly_followup_summary_week",
        "crm_weekly_followup_summary",
        ["week_start", "week_end"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_summary_type_dept",
        "crm_weekly_followup_summary",
        ["summary_type", "department_name"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_summary_dept_id",
        "crm_weekly_followup_summary",
        ["department_id"],
        unique=False,
    )

    op.create_table(
        "crm_weekly_followup_entity_summary",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=True),
        sa.Column("department_name", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("account_id", sa.String(length=255), nullable=True),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("opportunity_id", sa.String(length=255), nullable=True),
        sa.Column("opportunity_name", sa.String(length=255), nullable=True),
        sa.Column("partner_id", sa.String(length=255), nullable=True),
        sa.Column("partner_name", sa.String(length=255), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("progress", sa.Text(), nullable=True),
        sa.Column("risks", sa.Text(), nullable=True),
        sa.Column("evidence_record_ids", sa.Text(), nullable=True),
        sa.Column("comments", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_start",
            "week_end",
            "department_name",
            "entity_type",
            "entity_id",
            name="ux_crm_weekly_followup_entity",
        ),
    )
    op.create_index(
        "idx_weekly_followup_entity_week",
        "crm_weekly_followup_entity_summary",
        ["week_start", "week_end"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_entity_dept",
        "crm_weekly_followup_entity_summary",
        ["department_name"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_entity_dept_id",
        "crm_weekly_followup_entity_summary",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_entity_entity",
        "crm_weekly_followup_entity_summary",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_entity_owner",
        "crm_weekly_followup_entity_summary",
        ["owner_user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_weekly_followup_entity_owner", table_name="crm_weekly_followup_entity_summary")
    op.drop_index("idx_weekly_followup_entity_entity", table_name="crm_weekly_followup_entity_summary")
    op.drop_index("idx_weekly_followup_entity_dept", table_name="crm_weekly_followup_entity_summary")
    op.drop_index("idx_weekly_followup_entity_dept_id", table_name="crm_weekly_followup_entity_summary")
    op.drop_index("idx_weekly_followup_entity_week", table_name="crm_weekly_followup_entity_summary")
    op.drop_table("crm_weekly_followup_entity_summary")

    op.drop_index("idx_weekly_followup_summary_dept_id", table_name="crm_weekly_followup_summary")
    op.drop_index("idx_weekly_followup_summary_type_dept", table_name="crm_weekly_followup_summary")
    op.drop_index("idx_weekly_followup_summary_week", table_name="crm_weekly_followup_summary")
    op.drop_table("crm_weekly_followup_summary")



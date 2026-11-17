"""add crm weekly followup leader engagement

Revision ID: 69ac02144005
Revises: 8c3d0d0f6a12
Create Date: 2026-02-24

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "69ac02144005"
down_revision: str = "8c3d0d0f6a12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_weekly_followup_leader_engagement",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("summary_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("department_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("leader_user_id", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("commented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "summary_id",
            "leader_user_id",
            name="ux_weekly_followup_leader_engagement",
        ),
    )
    op.create_index(
        "idx_weekly_followup_leader_engagement_week_dept",
        "crm_weekly_followup_leader_engagement",
        ["week_start", "week_end", "department_name"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_leader_engagement_week_dept_id",
        "crm_weekly_followup_leader_engagement",
        ["week_start", "week_end", "department_id"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_leader_engagement_leader_reviewed",
        "crm_weekly_followup_leader_engagement",
        ["leader_user_id", "reviewed_at"],
        unique=False,
    )
    op.create_index(
        "idx_weekly_followup_leader_engagement_summary",
        "crm_weekly_followup_leader_engagement",
        ["summary_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_weekly_followup_leader_engagement_summary", table_name="crm_weekly_followup_leader_engagement")
    op.drop_index(
        "idx_weekly_followup_leader_engagement_leader_reviewed",
        table_name="crm_weekly_followup_leader_engagement",
    )
    op.drop_index(
        "idx_weekly_followup_leader_engagement_week_dept_id",
        table_name="crm_weekly_followup_leader_engagement",
    )
    op.drop_index(
        "idx_weekly_followup_leader_engagement_week_dept",
        table_name="crm_weekly_followup_leader_engagement",
    )
    op.drop_table("crm_weekly_followup_leader_engagement")


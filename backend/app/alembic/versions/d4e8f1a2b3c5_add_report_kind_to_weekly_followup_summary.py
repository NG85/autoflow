"""add report_kind to crm_weekly_followup_summary

Revision ID: d4e8f1a2b3c5
Revises: c9f3b1a7d2e8
Create Date: 2026-09-21 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "d4e8f1a2b3c5"
down_revision = "c9f3b1a7d2e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_weekly_followup_summary",
        sa.Column(
            "report_kind",
            sa.String(length=32),
            nullable=False,
            server_default="followup",
            comment="报告种类：followup=周跟进总结，visit_report=周拜访报告",
        ),
    )
    # TiDB/MySQL 上 UniqueConstraint 实现为 unique index
    op.drop_index("ux_crm_weekly_followup_summary", table_name="crm_weekly_followup_summary")
    op.create_index(
        "ux_crm_weekly_followup_summary",
        "crm_weekly_followup_summary",
        ["week_start", "week_end", "summary_type", "department_name", "report_kind"],
        unique=True,
    )
    op.create_index(
        "idx_weekly_followup_summary_report_kind",
        "crm_weekly_followup_summary",
        ["report_kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_weekly_followup_summary_report_kind", table_name="crm_weekly_followup_summary")
    op.drop_index("ux_crm_weekly_followup_summary", table_name="crm_weekly_followup_summary")
    op.create_index(
        "ux_crm_weekly_followup_summary",
        "crm_weekly_followup_summary",
        ["week_start", "week_end", "summary_type", "department_name"],
        unique=True,
    )
    op.drop_column("crm_weekly_followup_summary", "report_kind")

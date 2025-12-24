"""add crm_todos_weekly_metrics table

Revision ID: e1c3a9b4d2f0
Revises: f3b8e4d4c9a1
Create Date: 2025-12-24

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "e1c3a9b4d2f0"
down_revision = "f3b8e4d4c9a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_todos_weekly_metrics",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column("assignee_id", sa.String(length=100), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("data_source", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
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
            "week_start",
            "week_end",
            "assignee_id",
            "department_id",
            "metric",
            "data_source",
            name="ux_crm_todos_weekly_metrics",
        ),
    )

    # 查询索引：按周汇总（部门/公司）
    op.create_index(
        op.f("ix_week_start_week_end_dept_id_metric_data_source"),
        "crm_todos_weekly_metrics",
        ["week_start", "week_end", "department_id", "metric", "data_source"],
        unique=False,
    )
    op.create_index(
        op.f("ix_week_start_week_end_metric_data_source"),
        "crm_todos_weekly_metrics",
        ["week_start", "week_end", "metric", "data_source"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_week_start_week_end_metric_data_source"),
        table_name="crm_todos_weekly_metrics",
    )
    op.drop_index(
        op.f("ix_week_start_week_end_dept_id_metric_data_source"),
        table_name="crm_todos_weekly_metrics",
    )
    op.drop_table("crm_todos_weekly_metrics")


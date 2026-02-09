"""add crm_todo_metrics_facts (EAV) table

Revision ID: 8c3d0d0f6a12
Revises: 7b3d8a1f2c10
Create Date: 2026-02-06

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "8c3d0d0f6a12"
down_revision: str = "7b3d8a1f2c10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_todo_metrics_facts",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("anchor", sa.String(length=20), nullable=False),
        sa.Column("grain", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("hour_of_day", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=100), nullable=False),
        sa.Column("data_source", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("value_int", sa.Integer(), nullable=False, server_default="0"),
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
            "anchor",
            "grain",
            "period_start",
            "period_end",
            "hour_of_day",
            "subject_type",
            "subject_id",
            "data_source",
            "metric",
            name="ux_crm_todo_metrics_facts",
        ),
    )

    # 常用查询索引：按时间范围 + 口径 + 指标（含 data_source）
    op.create_index(
        "ix_crm_todo_metrics_facts_anchor_grain_period_metric_ds",
        "crm_todo_metrics_facts",
        ["anchor", "grain", "period_start", "period_end", "metric", "data_source"],
        unique=False,
    )
    op.create_index(
        "ix_crm_todo_metrics_facts_subject_period_metric",
        "crm_todo_metrics_facts",
        ["subject_type", "subject_id", "period_start", "metric"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_crm_todo_metrics_facts_subject_period_metric", table_name="crm_todo_metrics_facts")
    op.drop_index("ix_crm_todo_metrics_facts_anchor_grain_period_metric_ds", table_name="crm_todo_metrics_facts")
    op.drop_table("crm_todo_metrics_facts")


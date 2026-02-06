"""add crm_visit_metrics_facts (EAV) table

Revision ID: 7b3d8a1f2c10
Revises: 6f2a1c9d3b21
Create Date: 2026-02-05

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "7b3d8a1f2c10"
down_revision: str = "6f2a1c9d3b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_visit_metrics_facts",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("anchor", sa.String(length=20), nullable=False),
        sa.Column("grain", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=100), nullable=False),
        sa.Column("department_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("department_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("weekday_iso", sa.Integer(), nullable=False, server_default="0"),
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
            "subject_type",
            "subject_id",
            "department_id",
            "metric",
            "weekday_iso",
            name="ux_crm_visit_metrics_facts",
        ),
    )

    # 常用查询索引：按时间范围 + 口径 + 指标
    op.create_index(
        "ix_crm_visit_metrics_facts_anchor_grain_period_metric",
        "crm_visit_metrics_facts",
        ["anchor", "grain", "period_start", "period_end", "metric"],
        unique=False,
    )
    op.create_index(
        "ix_crm_visit_metrics_facts_subject_period",
        "crm_visit_metrics_facts",
        ["subject_type", "subject_id", "period_start", "period_end"],
        unique=False,
    )
    op.create_index(
        "ix_crm_visit_metrics_facts_dept_period_metric",
        "crm_visit_metrics_facts",
        ["department_id", "period_start", "period_end", "metric"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crm_visit_metrics_facts_dept_period_metric",
        table_name="crm_visit_metrics_facts",
    )
    op.drop_index(
        "ix_crm_visit_metrics_facts_subject_period",
        table_name="crm_visit_metrics_facts",
    )
    op.drop_index(
        "ix_crm_visit_metrics_facts_anchor_grain_period_metric",
        table_name="crm_visit_metrics_facts",
    )
    op.drop_table("crm_visit_metrics_facts")


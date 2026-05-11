"""add feishu billing usage report for reconciliation

Revision ID: c4e8b1a2f3d0
Revises: 6d9f2e1a7b34
Create Date: 2026-05-11

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision: str = "c4e8b1a2f3d0"
down_revision: str = "6d9f2e1a7b34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feishu_billing_usage_report",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=220), nullable=False),
        sa.Column("ai_module_key", sa.String(length=128), nullable=False),
        sa.Column("operator", sa.String(length=128), nullable=False),
        sa.Column("review_detail", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_api_code", sa.Integer(), nullable=True),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_exception_type", sa.String(length=200), nullable=True),
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
        sa.UniqueConstraint("trace_id", name="ux_feishu_billing_usage_report_trace"),
    )
    op.create_index(
        "idx_feishu_billing_usage_status_created",
        "feishu_billing_usage_report",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_feishu_billing_usage_module_created",
        "feishu_billing_usage_report",
        ["ai_module_key", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_feishu_billing_usage_module_created",
        table_name="feishu_billing_usage_report",
    )
    op.drop_index(
        "idx_feishu_billing_usage_status_created",
        table_name="feishu_billing_usage_report",
    )
    op.drop_table("feishu_billing_usage_report")

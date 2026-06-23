"""add notification_cc_rules tables

Revision ID: a9f2e8c1d4b7
Revises: c3a8f1e2b904
Create Date: 2026-06-22 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a9f2e8c1d4b7"
down_revision = "c3a8f1e2b904"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_cc_rules",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_user_id", sa.CHAR(length=32), nullable=True),
        sa.Column("scope_department_id", sa.String(length=255), nullable=True),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["scope_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_cc_rules_event_scope_user_enabled",
        "notification_cc_rules",
        ["event_type", "scope_type", "scope_user_id", "enabled"],
    )
    op.create_index(
        "ix_notification_cc_rules_event_scope_enabled",
        "notification_cc_rules",
        ["event_type", "scope_type", "enabled"],
    )

    op.create_table(
        "notification_cc_rule_recipients",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.CHAR(length=32), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["notification_cc_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "user_id", name="uq_notification_cc_rule_recipient"),
    )
    op.create_index(
        "ix_notification_cc_rule_recipients_rule_id",
        "notification_cc_rule_recipients",
        ["rule_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_cc_rule_recipients_rule_id", table_name="notification_cc_rule_recipients")
    op.drop_table("notification_cc_rule_recipients")
    op.drop_index("ix_notification_cc_rules_event_scope_enabled", table_name="notification_cc_rules")
    op.drop_index("ix_notification_cc_rules_event_scope_user_enabled", table_name="notification_cc_rules")
    op.drop_table("notification_cc_rules")

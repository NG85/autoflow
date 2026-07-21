"""add include_children to notification_cc_rules

Revision ID: b4e7c2a9f1d8
Revises: f1b2c3d4e5a6
Create Date: 2026-07-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b4e7c2a9f1d8"
down_revision = "f1b2c3d4e5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_cc_rules",
        sa.Column(
            "include_children",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_notification_cc_rules_event_scope_dept_enabled",
        "notification_cc_rules",
        ["event_type", "scope_type", "scope_department_id", "enabled"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_cc_rules_event_scope_dept_enabled",
        table_name="notification_cc_rules",
    )
    op.drop_column("notification_cc_rules", "include_children")

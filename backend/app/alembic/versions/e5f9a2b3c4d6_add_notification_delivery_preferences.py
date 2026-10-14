"""add notification_delivery_preferences

Revision ID: e5f9a2b3c4d6
Revises: d4e8f1a2b3c5
Create Date: 2026-09-21 14:20:00.000000

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "e5f9a2b3c4d6"
down_revision = "d4e8f1a2b3c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_delivery_preferences",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column("scene", sa.String(length=64), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("department_id", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("receive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "user_id",
            "scene",
            "variant",
            "department_id",
            name="ux_notification_delivery_pref",
        ),
    )
    op.create_index(
        "ix_notification_delivery_pref_user",
        "notification_delivery_preferences",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_delivery_pref_scene",
        "notification_delivery_preferences",
        ["scene"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_pref_scene", table_name="notification_delivery_preferences")
    op.drop_index("ix_notification_delivery_pref_user", table_name="notification_delivery_preferences")
    op.drop_table("notification_delivery_preferences")

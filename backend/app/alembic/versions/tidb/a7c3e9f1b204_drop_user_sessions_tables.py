"""drop user_sessions and archive table

Revision ID: a7c3e9f1b204
Revises: f1a2b3c4d5e6
Create Date: 2026-07-02 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "a7c3e9f1b204"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

ARCHIVE_TABLE = "user_sessions_archive_20260522"


def _drop_if_exists(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name in inspector.get_table_names():
        op.drop_table(table_name)


def upgrade() -> None:
    _drop_if_exists("user_sessions")
    _drop_if_exists(ARCHIVE_TABLE)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_sessions" in inspector.get_table_names():
        return

    op.create_table(
        "user_sessions",
        sa.Column("token", sqlmodel.sql.sqltypes.AutoString(length=43), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("user_id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token"),
    )

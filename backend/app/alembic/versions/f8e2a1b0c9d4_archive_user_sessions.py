"""archive user_sessions to user_sessions_archive_20260522

Revision ID: f8e2a1b0c9d4
Revises: e7a3c1d92b40
Create Date: 2026-05-22 10:00:00.000000

Move historical user_sessions rows to archive table; leave empty user_sessions for ORM.
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "f8e2a1b0c9d4"
down_revision = "e7a3c1d92b40"
branch_labels = None
depends_on = None

ARCHIVE_TABLE = "user_sessions_archive_20260522"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_sessions" not in inspector.get_table_names():
        return

    op.rename_table("user_sessions", ARCHIVE_TABLE)

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


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if ARCHIVE_TABLE not in inspector.get_table_names():
        return

    if "user_sessions" in inspector.get_table_names():
        op.drop_table("user_sessions")
    op.rename_table(ARCHIVE_TABLE, "user_sessions")

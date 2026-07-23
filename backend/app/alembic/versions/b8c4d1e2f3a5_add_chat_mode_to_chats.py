"""add chat_mode to chats table

Revision ID: b8c4d1e2f3a5
Revises: a3c7e9f1b204
Create Date: 2026-07-23 11:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "b8c4d1e2f3a5"
down_revision = "a3c7e9f1b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column(
            "chat_mode",
            sa.String(length=50),
            nullable=False,
            server_default="default",
            comment="会话模式（创建时写入，应用层 ChatMode 枚举约束）",
        ),
    )
    op.create_index("idx_chats_chat_mode", "chats", ["chat_mode"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_chats_chat_mode", table_name="chats")
    op.drop_column("chats", "chat_mode")

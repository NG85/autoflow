"""add review_session to chat_type enum

Revision ID: a1b2c3d4e5f6
Revises: b8d9f2c4a7e0
Create Date: 2026-04-07 11:30:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "b8d9f2c4a7e0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE chats MODIFY COLUMN chat_type "
        "ENUM('default', 'client_visit_guide', 'review_session') "
        "NOT NULL DEFAULT 'default'"
    )


def downgrade():
    op.execute(
        "ALTER TABLE chats MODIFY COLUMN chat_type "
        "ENUM('default', 'client_visit_guide') "
        "NOT NULL DEFAULT 'default'"
    )

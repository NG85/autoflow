"""add chat type to chats table

Revision ID: 630bbda8b207
Revises: 088e5cd3d91d
Create Date: 2025-04-02 10:20:53.995506

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = '630bbda8b207'
down_revision = '088e5cd3d91d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column('chat_type', sa.Enum('default', 'client_visit_guide', name='chat_type'), nullable=False))


def downgrade():
    op.drop_column('chats', 'chat_type')

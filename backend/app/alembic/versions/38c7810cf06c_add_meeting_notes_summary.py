"""add_meeting_notes_summary

Revision ID: 38c7810cf06c
Revises: 9ff94839d3eb
Create Date: 2025-08-14 11:18:09.196378

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = '38c7810cf06c'
down_revision = '9ff94839d3eb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加会议纪要总结字段
    op.add_column('document_contents', sa.Column('meeting_summary', mysql.MEDIUMTEXT(), nullable=True))
    op.add_column('document_contents', sa.Column('summary_status', sa.String(20), nullable=True))
    
    # 添加索引
    op.create_index('idx_summary_status', 'document_contents', ['summary_status'])


def downgrade() -> None:
    # 删除索引
    op.drop_index('idx_summary_status', table_name='document_contents')
    
    # 删除字段
    op.drop_column('document_contents', 'summary_status')
    op.drop_column('document_contents', 'meeting_summary')
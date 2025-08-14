"""create document_contents table

Revision ID: c89ab72bcedb
Revises: 68bcb55cbb93
Create Date: 2025-08-06 10:50:01.981756

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c89ab72bcedb'
down_revision = '68bcb55cbb93'
branch_labels = None
depends_on = None


def upgrade():
    # Create document_contents table
    op.create_table(
        'document_contents',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True, comment='自增主键ID'),
        sa.Column('user_id', sqlmodel.sql.sqltypes.GUID(), nullable=False, comment='用户ID'),
        sa.Column('visit_record_id', sa.String(128), nullable=True, comment='关联的拜访记录ID'),
        sa.Column('document_type', sa.String(50), nullable=False, comment='文档类型: feishu_doc, feishu_minute, file'),
        sa.Column('source_url', sa.Text(), nullable=False, comment='原文档URL'),
        sa.Column('raw_content', mysql.MEDIUMTEXT(), nullable=False, comment='原始文档内容'),
        sa.Column('title', sa.String(500), nullable=True, comment='文档标题'),
        sa.Column('file_size', sa.Integer(), nullable=True, comment='文件大小(字节)'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.current_timestamp(), onupdate=sa.func.current_timestamp(), nullable=False, comment='更新时间'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create foreign key constraint for user_id
    op.create_foreign_key(
        'fk_document_contents_user_id',
        'document_contents',
        'users',
        ['user_id'],
        ['id']
    )

    # Create indexes
    op.create_index('idx_user_id', 'document_contents', ['user_id'], unique=False)
    op.create_index('idx_document_type', 'document_contents', ['document_type'], unique=False)
    op.create_index('idx_visit_record_id', 'document_contents', ['visit_record_id'], unique=False)
    op.create_index('idx_created_at', 'document_contents', ['created_at'], unique=False)
    op.create_index('idx_user_type', 'document_contents', ['user_id', 'document_type'], unique=False)


def downgrade():
    # Drop indexes
    op.drop_index('idx_user_type', table_name='document_contents')
    op.drop_index('idx_created_at', table_name='document_contents')
    op.drop_index('idx_visit_record_id', table_name='document_contents')
    op.drop_index('idx_document_type', table_name='document_contents')
    op.drop_index('idx_user_id', table_name='document_contents')

    # Drop foreign key constraint
    op.drop_constraint('fk_document_contents_user_id', 'document_contents', type_='foreignkey')

    # Drop table
    op.drop_table('document_contents') 
"""add_risk_info_to_document_contents

Revision ID: 3001941b30f8
Revises: 46932d1cbc3d
Create Date: 2026-01-20 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '3001941b30f8'
down_revision = '46932d1cbc3d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加风险信息字段
    op.add_column(
        'document_contents',
        sa.Column('risk_info', mysql.MEDIUMTEXT(), nullable=True, comment='从文档内容中提取的风险信息')
    )
    op.add_column(
        'document_contents',
        sa.Column('risk_extract_status', sa.String(20), nullable=True, comment='风险信息提取状态: success, failed')
    )
    
    # 添加索引，便于按提取状态筛选
    op.create_index(
        'idx_risk_extract_status',
        'document_contents',
        ['risk_extract_status'],
        unique=False
    )


def downgrade() -> None:
    # 删除索引
    op.drop_index('idx_risk_extract_status', table_name='document_contents')
    
    # 删除字段
    op.drop_column('document_contents', 'risk_extract_status')
    op.drop_column('document_contents', 'risk_info')

"""add platform in profile

Revision ID: 6a22c062ecee
Revises: 38c7810cf06c
Create Date: 2025-08-15 17:07:47.216576

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '6a22c062ecee'
down_revision = '38c7810cf06c'
branch_labels = None
depends_on = None


def upgrade():
    # 1. 添加新字段
    op.add_column('user_profiles', sa.Column('platform', sa.String(50), nullable=True))
    op.add_column('user_profiles', sa.Column('open_id', sa.String(255), nullable=True))
    op.add_column('user_profiles', sa.Column('notification_tags', sa.String(1000), nullable=True))
    
    # 2. 创建索引
    op.create_index('ix_user_profiles_platform', 'user_profiles', ['platform'])
    op.create_index('ix_user_profiles_open_id', 'user_profiles', ['open_id'])


def downgrade():        
    # 1. 删除索引
    op.drop_index('ix_user_profiles_open_id', 'user_profiles')
    op.drop_index('ix_user_profiles_platform', 'user_profiles')
    
    # 2. 删除新字段
    op.drop_column('user_profiles', 'notification_tags')
    op.drop_column('user_profiles', 'open_id')
    op.drop_column('user_profiles', 'platform')

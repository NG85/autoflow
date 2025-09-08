"""update_collaborative_participants_to_text

Revision ID: b13a542ede4d
Revises: 9f1430bc28cf
Create Date: 2025-09-01 14:32:54.743282

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b13a542ede4d'
down_revision = '9f1430bc28cf'
branch_labels = None
depends_on = None


def upgrade():
    """将 collaborative_participants 字段从 VARCHAR 转换为 TEXT 类型"""
    # 使用TEXT类型提供更好的向后兼容性
    # TEXT类型可以存储任意长度的字符串，包括JSON格式
    op.execute("""
        ALTER TABLE crm_sales_visit_records 
        MODIFY COLUMN collaborative_participants TEXT NULL
    """)


def downgrade():
    """将 collaborative_participants 字段从 TEXT 转换回 VARCHAR 类型"""
    op.execute("""
        ALTER TABLE crm_sales_visit_records 
        MODIFY COLUMN collaborative_participants VARCHAR(255) NULL
    """)

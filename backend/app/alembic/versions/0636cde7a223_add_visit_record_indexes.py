"""add visit record indexes

Revision ID: 0636cde7a223
Revises: be8954aab164
Create Date: 2025-11-13 14:21:10.306206

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0636cde7a223'
down_revision = 'be8954aab164'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add indexes for crm_sales_visit_records table
    """
    
    # ============================================
    # 单列索引 - 高频过滤字段
    # ============================================
    
    # recorder_id 索引（权限过滤的关键字段，几乎每次查询都会使用）
    op.create_index(
        'idx_visit_record_recorder_id',
        'crm_sales_visit_records',
        ['recorder_id'],
        unique=False
    )
    
    # account_id 索引（客户ID过滤，用于精确匹配）
    op.create_index(
        'idx_visit_record_account_id',
        'crm_sales_visit_records',
        ['account_id'],
        unique=False
    )
    
    # partner_name 索引（合作伙伴名称过滤，用于精确匹配）
    op.create_index(
        'idx_visit_record_partner_name',
        'crm_sales_visit_records',
        ['partner_name'],
        unique=False
    )
    
    # last_modified_time 索引（修改时间范围过滤）
    op.create_index(
        'idx_visit_record_last_modified_time',
        'crm_sales_visit_records',
        ['last_modified_time'],
        unique=False
    )
    
    # ============================================
    # 复合索引 - 优化常见查询模式
    # ============================================
    
    # recorder_id + visit_communication_date 复合索引
    # 用途：最常见的查询模式是"权限过滤 + 按日期排序"
    # 此索引可以同时满足 WHERE recorder_id IN (...) 和 ORDER BY visit_communication_date DESC
    op.create_index(
        'idx_visit_record_recorder_date',
        'crm_sales_visit_records',
        ['recorder_id', sa.text('visit_communication_date DESC')],
        unique=False
    )


def downgrade():
    """
    Drop indexes for crm_sales_visit_records table
    """
    # 复合索引
    op.drop_index('idx_visit_record_recorder_date', table_name='crm_sales_visit_records')
    
    # 单列索引
    op.drop_index('idx_visit_record_last_modified_time', table_name='crm_sales_visit_records')
    op.drop_index('idx_visit_record_partner_name', table_name='crm_sales_visit_records')
    op.drop_index('idx_visit_record_account_id', table_name='crm_sales_visit_records')
    op.drop_index('idx_visit_record_recorder_id', table_name='crm_sales_visit_records')


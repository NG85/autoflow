"""add_locations_to_visit_records

Revision ID: 0ea41d2c0ce7
Revises: 0636cde7a223
Create Date: 2025-11-27 10:14:20.181828

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DECIMAL


# revision identifiers, used by Alembic.
revision = '0ea41d2c0ce7'
down_revision = '0636cde7a223'
branch_labels = None
depends_on = None


def upgrade():
    # Add latitude column to crm_sales_visit_records table
    # DECIMAL(10, 7) allows for values from -90.0000000 to 90.0000000
    op.add_column(
        'crm_sales_visit_records',
        sa.Column('latitude', DECIMAL(10, 7), nullable=True, comment='纬度，范围 -90 到 90')
    )
    
    # Add longitude column to crm_sales_visit_records table
    # DECIMAL(11, 7) allows for values from -180.0000000 to 180.0000000
    op.add_column(
        'crm_sales_visit_records',
        sa.Column('longitude', DECIMAL(11, 7), nullable=True, comment='经度，范围 -180 到 180')
    )


def downgrade():
    # Drop longitude column from crm_sales_visit_records table
    op.drop_column('crm_sales_visit_records', 'longitude')
    
    # Drop latitude column from crm_sales_visit_records table
    op.drop_column('crm_sales_visit_records', 'latitude')

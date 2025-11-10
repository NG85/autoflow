"""add_record_type_to_visit_records

Revision ID: 1a80cdc3c768
Revises: 141cdff2d5fc
Create Date: 2025-11-10 13:55:12.432996

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = '1a80cdc3c768'
down_revision = '141cdff2d5fc'
branch_labels = None
depends_on = None


def upgrade():
    # Add record_type column to crm_sales_visit_records table
    op.add_column('crm_sales_visit_records', sa.Column('record_type', sa.String(length=50), nullable=True))


def downgrade():
    # Drop record_type column from crm_sales_visit_records table
    op.drop_column('crm_sales_visit_records', 'record_type')

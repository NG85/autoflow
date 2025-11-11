"""add_visit_purpose_to_visit_records

Revision ID: be8954aab164
Revises: 1a80cdc3c768
Create Date: 2025-11-11 14:21:10.306206

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = 'be8954aab164'
down_revision = '1a80cdc3c768'
branch_labels = None
depends_on = None


def upgrade():
    # Add visit_purpose column to crm_sales_visit_records table
    op.add_column('crm_sales_visit_records', sa.Column('visit_purpose', sa.String(length=255), nullable=True))


def downgrade():
    # Drop visit_purpose column from crm_sales_visit_records table
    op.drop_column('crm_sales_visit_records', 'visit_purpose')

"""change_attachment_field_to_text

Revision ID: d1776949f5f5
Revises: c89ab72bcedb
Create Date: 2025-08-07 17:51:25.979080

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = 'd1776949f5f5'
down_revision = 'c89ab72bcedb'
branch_labels = None
depends_on = None


def upgrade():
    # 将 attachment 字段从 VARCHAR(255) 改为 TEXT
    op.alter_column(
        'crm_sales_visit_records',
        'attachment',
        existing_type=sa.String(length=255),
        type_=sa.MEDIUMTEXT(),
        existing_nullable=True
    )


def downgrade():
    # 将 attachment 字段从 TEXT 改回 VARCHAR(255)
    op.alter_column(
        'crm_sales_visit_records',
        'attachment',
        existing_type=sa.MEDIUMTEXT(),
        type_=sa.String(length=255),
        existing_nullable=True
    )

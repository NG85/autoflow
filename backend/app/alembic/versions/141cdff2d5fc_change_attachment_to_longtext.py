"""change_attachment_to_longtext

Revision ID: 141cdff2d5fc
Revises: bd55b5ef14b4
Create Date: 2025-11-03 17:22:55.910819

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '141cdff2d5fc'
down_revision = 'bd55b5ef14b4'
branch_labels = None
depends_on = None


def upgrade():
    # 将 attachment 字段从 MEDIUMTEXT 改为 LONGTEXT (MySQL)
    op.alter_column(
        'crm_sales_visit_records',
        'attachment',
        existing_type=mysql.MEDIUMTEXT(),
        type_=mysql.LONGTEXT(),
        existing_nullable=True
    )


def downgrade():
    # 将 attachment 字段从 LONGTEXT 改回 MEDIUMTEXT
    op.alter_column(
        'crm_sales_visit_records',
        'attachment',
        existing_type=mysql.LONGTEXT(),
        type_=mysql.MEDIUMTEXT(),
        existing_nullable=True
    )

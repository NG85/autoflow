"""add_contacts_to_crm_sales_visit_records

Revision ID: b27356d37c0f
Revises: 1857317f8a86
Create Date: 2026-01-20 10:58:55.154841

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = 'b27356d37c0f'
down_revision = '1857317f8a86'
branch_labels = None
depends_on = None


def upgrade():
    # 添加contacts字段（JSON类型），用于存储多个联系人
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("contacts", sa.JSON(), nullable=True, comment="联系人列表（支持多个联系人，JSON数组）"),
    )


def downgrade():
    op.drop_column("crm_sales_visit_records", "contacts")

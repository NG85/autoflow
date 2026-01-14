"""add_contact_id_to_crm_sales_visit_records

Revision ID: 1857317f8a86
Revises: a2f75cf740ee
Create Date: 2026-01-13 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = '1857317f8a86'
down_revision = 'a2f75cf740ee'
branch_labels = None
depends_on = None


def upgrade():
    # 使用原始 SQL 添加列，指定位置在 contact_name 之后
    op.execute(
        "ALTER TABLE crm_sales_visit_records "
        "ADD COLUMN contact_id VARCHAR(255) NULL "
        "COMMENT '联系人ID（关联local_contacts或crm_contacts的unique_id）' "
        "AFTER contact_name"
    )
    op.create_index(
        "idx_contact_id",
        "crm_sales_visit_records",
        ["contact_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_contact_id", table_name="crm_sales_visit_records")
    op.drop_column("crm_sales_visit_records", "contact_id")

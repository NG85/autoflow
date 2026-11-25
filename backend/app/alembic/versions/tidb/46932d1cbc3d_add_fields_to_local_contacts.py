"""add_fields_to_local_contacts

Revision ID: 46932d1cbc3d
Revises: b27356d37c0f
Create Date: 2026-01-20 13:51:24.664506

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = '46932d1cbc3d'
down_revision = 'b27356d37c0f'
branch_labels = None
depends_on = None


def upgrade():
    # 添加部门字段
    op.add_column(
        "local_contacts",
        sa.Column("department", sa.String(length=255), nullable=True, comment="部门"),
    )
    
    # 添加上级主管字段
    op.add_column(
        "local_contacts",
        sa.Column("direct_superior", sa.String(length=255), nullable=True, comment="直属上级"),
    )
    
    # 添加状态字段（在职状态）
    op.add_column(
        "local_contacts",
        sa.Column("status", sa.String(length=255), nullable=True, comment="状态"),
    )
    
    # 添加商务关系字段
    op.add_column(
        "local_contacts",
        sa.Column("business_relationship", sa.String(length=255), nullable=True, comment="商务关系"),
    )


def downgrade():
    # 删除添加的字段
    op.drop_column("local_contacts", "business_relationship")
    op.drop_column("local_contacts", "status")
    op.drop_column("local_contacts", "direct_superior")
    op.drop_column("local_contacts", "department")    

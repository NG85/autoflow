"""add_file_permission_table

Revision ID: 8c5b4a7a7945
Revises: 630bbda8b207
Create Date: 2025-05-16 10:35:14.115171

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = '8c5b4a7a7945'
down_revision: Union[str, None] = '630bbda8b207'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建文件权限表
    op.create_table(
        'file_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('file_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sqlmodel.sql.sqltypes.GUID(), nullable=True),
        sa.Column('permission_type', sa.Enum('read', 'owner', name='permissiontype'), nullable=False, server_default='read'),
        sa.Column('granted_by', sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['file_id'], ['uploads.id'], ),
        sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 创建索引
    op.create_index(op.f('ix_file_permissions_file_id'), 'file_permissions', ['file_id'], unique=False)
    op.create_index(op.f('ix_file_permissions_user_id'), 'file_permissions', ['user_id'], unique=False)
    op.create_index(op.f('ix_file_permissions_file_id_user_id'), 'file_permissions', ['file_id', 'user_id'], unique=False)


def downgrade() -> None:
    # 删除索引
    op.drop_index(op.f('ix_file_permissions_file_id_user_id'), table_name='file_permissions')
    op.drop_index(op.f('ix_file_permissions_user_id'), table_name='file_permissions')
    op.drop_index(op.f('ix_file_permissions_file_id'), table_name='file_permissions')

    # 删除表
    op.drop_table('file_permissions')


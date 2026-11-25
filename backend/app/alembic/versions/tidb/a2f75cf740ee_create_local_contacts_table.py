"""create_local_contacts_table

Revision ID: a2f75cf740ee
Revises: 1c4f8a2d7e90
Create Date: 2026-01-13 14:46:07.518927

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2f75cf740ee'
down_revision = '1c4f8a2d7e90'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "local_contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, primary_key=True, comment="主键ID（自增）"),
        sa.Column("unique_id", sa.String(length=255), nullable=False, comment="唯一性ID（必填）"),
        sa.Column("name", sa.String(length=255), nullable=False, comment="联系人姓名（必填）"),
        sa.Column("customer_id", sa.String(length=255), nullable=False, comment="客户ID（关联crm_accounts.unique_id，必填）"),
        sa.Column("customer_name", sa.String(length=255), nullable=False, comment="客户名称（必填）"),
        sa.Column("position", sa.String(length=255), nullable=False, comment="联系人职位（必填）"),
        sa.Column("gender", sa.String(length=10), nullable=True, comment="性别"),
        sa.Column("mobile", sa.String(length=255), nullable=True, comment="手机"),
        sa.Column("phone", sa.String(length=255), nullable=True, comment="电话"),
        sa.Column("email",sa.String(length=255), nullable=True, comment="邮件"),
        sa.Column("wechat", sa.String(length=255), nullable=True, comment="微信"),
        sa.Column("address", sa.String(length=512), nullable=True, comment="联系地址"),
        sa.Column("key_decision_maker", sa.Boolean(), nullable=True, comment="是否为关键决策人"),
        sa.Column("source", sa.String(length=255), nullable=True, comment="来源"),
        sa.Column("remarks", sa.Text(), nullable=True, comment="备注"),
        sa.Column("delete_flag", sa.Boolean(), nullable=True, comment="删除标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), onupdate=sa.text("now()"), nullable=True, comment="更新时间"),
        sa.Column("created_by", sa.CHAR(length=32), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.CHAR(length=32), nullable=True, comment="最后修改人ID"),
        sa.Column("crm_unique_id", sa.String(length=255), nullable=True, comment="CRM系统唯一ID（回写后填充）"),
        sa.Column("synced_to_crm", sa.Boolean(), nullable=True, comment="是否已同步到CRM"),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True, comment="同步到CRM的时间"),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # 创建索引
    op.create_index(
        "idx_local_contacts_customer_id",
        "local_contacts",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "idx_local_contacts_customer_name",
        "local_contacts",
        ["customer_name"],
        unique=False,
    )
    op.create_index(
        "idx_local_contacts_delete_flag",
        "local_contacts",
        ["delete_flag"],
        unique=False,
    )
    op.create_index(
        "idx_local_contacts_name",
        "local_contacts",
        ["name"],
        unique=False,
    )
    # 创建唯一索引
    op.create_index(
        "idx_local_contacts_unique_id",
        "local_contacts",
        ["unique_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("idx_local_contacts_unique_id", table_name="local_contacts")
    op.drop_index("idx_local_contacts_name", table_name="local_contacts")
    op.drop_index("idx_local_contacts_delete_flag", table_name="local_contacts")
    op.drop_index("idx_local_contacts_customer_name", table_name="local_contacts")
    op.drop_index("idx_local_contacts_customer_id", table_name="local_contacts")
    op.drop_table("local_contacts")

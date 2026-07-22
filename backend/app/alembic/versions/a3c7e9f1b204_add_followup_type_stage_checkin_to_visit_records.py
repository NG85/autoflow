"""add followup_type / followup_stage / field_check_in to visit records

Revision ID: a3c7e9f1b204
Revises: b4e7c2a9f1d8
Create Date: 2026-07-22 16:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a3c7e9f1b204"
down_revision = "b4e7c2a9f1d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_sales_visit_records",
        sa.Column(
            "followup_type",
            sa.String(length=100),
            nullable=True,
            comment="跟进类型（前端固定选项）",
        ),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column(
            "followup_stage",
            sa.String(length=100),
            nullable=True,
            comment="跟进阶段（前端固定选项；是否必填由前端按环境控制）",
        ),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column(
            "field_check_in_id",
            sa.String(length=255),
            nullable=True,
            comment="关联外勤打卡ID",
        ),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column(
            "field_check_in_name",
            sa.String(length=255),
            nullable=True,
            comment="关联外勤打卡名称",
        ),
    )


def downgrade() -> None:
    op.drop_column("crm_sales_visit_records", "field_check_in_name")
    op.drop_column("crm_sales_visit_records", "field_check_in_id")
    op.drop_column("crm_sales_visit_records", "followup_stage")
    op.drop_column("crm_sales_visit_records", "followup_type")

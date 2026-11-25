"""add crm_sales_visit_records revisions and revision_count

Revision ID: c3a8f1e2b904
Revises: f8e2a1b0c9d4
Create Date: 2026-06-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision = "c3a8f1e2b904"
down_revision = "f8e2a1b0c9d4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "crm_sales_visit_records",
        sa.Column(
            "revision_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="拜访记录修订次数",
        ),
    )
    op.create_table(
        "crm_sales_visit_records_revisions",
        sa.Column("id", sqlmodel.sql.sqltypes.GUID(), nullable=False),
        sa.Column(
            "record_id",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=False,
            comment="拜访记录业务 ID（crm_sales_visit_records.record_id）",
        ),
        sa.Column("revision_seq", sa.Integer(), nullable=False, comment="同 record 递增序号"),
        sa.Column(
            "revised_by_id",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
            comment="修订人 user_id",
        ),
        sa.Column(
            "revised_by_name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
            comment="修订人姓名快照",
        ),
        sa.Column("changes", sa.JSON(), nullable=False, comment="字段变更 [{field, old, new}]"),
        sa.Column(
            "aldebaran_message_type",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column(
            "aldebaran_dedupe_key",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "card_push_status",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=True,
            comment="本轮修订推卡状态",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_crm_sales_visit_records_revisions_record_id",
        "crm_sales_visit_records_revisions",
        ["record_id"],
    )
    op.create_index(
        "idx_crm_sales_visit_records_revisions_record_seq",
        "crm_sales_visit_records_revisions",
        ["record_id", "revision_seq"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "idx_crm_sales_visit_records_revisions_record_seq",
        table_name="crm_sales_visit_records_revisions",
    )
    op.drop_index(
        "idx_crm_sales_visit_records_revisions_record_id",
        table_name="crm_sales_visit_records_revisions",
    )
    op.drop_table("crm_sales_visit_records_revisions")
    op.drop_column("crm_sales_visit_records", "revision_count")

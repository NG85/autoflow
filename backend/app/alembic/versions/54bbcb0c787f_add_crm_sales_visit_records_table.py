"""add crm_sales_visit_records table

Revision ID: 54bbcb0c787f
Revises: 04947f9684ab
Create Date: 2025-07-14 17:35:05.720648

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = '54bbcb0c787f'
down_revision = '04947f9684ab'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_sales_visit_records",
       
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("account_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("opportunity_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("opportunity_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("partner_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("customer_lead_source", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("visit_object_category", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("contact_position", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("contact_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("recorder", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("collaborative_participants", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("visit_communication_date", sa.Date(), nullable=True),
        sa.Column("counterpart_location", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("visit_communication_method", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("communication_duration", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("expectation_achieved", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("followup_record", sa.Text(), nullable=True),
        sa.Column("followup_quality_level", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("followup_quality_reason", sa.Text(), nullable=True),
        sa.Column("next_steps", sa.Text(), nullable=True),
        sa.Column("next_steps_quality_level", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("next_steps_quality_reason", sa.Text(), nullable=True),
        sa.Column("attachment", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("parent_record", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("last_modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_id", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_account_name",
        "crm_sales_visit_records",
        ["account_name"],
        unique=False,
    )
    op.create_index(
        "idx_recorder",
        "crm_sales_visit_records",
        ["recorder"],
        unique=False,
    )
    op.create_index(
        "idx_visit_date",
        "crm_sales_visit_records",
        ["visit_communication_date"],
        unique=False,
    )

def downgrade():
    op.drop_index("idx_visit_date", table_name="crm_sales_visit_records")
    op.drop_index("idx_recorder", table_name="crm_sales_visit_records")
    op.drop_index("idx_account_name", table_name="crm_sales_visit_records")
    op.drop_table("crm_sales_visit_records")

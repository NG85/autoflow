"""add is_first_visit and recorder_id columns to crm_sales_visit_records table

Revision ID: 68bcb55cbb93
Revises: 54bbcb0c787f
Create Date: 2025-08-01 10:47:58.141786

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from tidb_vector.sqlalchemy import VectorType


# revision identifiers, used by Alembic.
revision = '68bcb55cbb93'
down_revision = '54bbcb0c787f'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_first_visit column to crm_sales_visit_records table
    op.add_column('crm_sales_visit_records', sa.Column('is_first_visit', sa.Boolean(), nullable=True))

    # Add recorder_id column to crm_sales_visit_records table
    op.add_column('crm_sales_visit_records', sa.Column('recorder_id', sqlmodel.sql.sqltypes.GUID(), nullable=True)) 

    # Add foreign key constraint for recorder_id column
    op.create_foreign_key(
        "fk_recorder_id",
        "crm_sales_visit_records",
        "users",
        ["recorder_id"],
        ["id"],
    )

    # Add index for is_first_visit column    
    op.create_index(
        "idx_is_first_visit",
        "crm_sales_visit_records",
        ["is_first_visit"],
        unique=False,
    )


def downgrade():
    # Drop index for is_first_visit column
    op.drop_index("idx_is_first_visit", table_name="crm_sales_visit_records")
    
    # Drop foreign key constraint for recorder_id column
    op.drop_constraint("fk_recorder_id", "crm_sales_visit_records", type_="foreignkey")
    
    # Drop recorder_id column from crm_sales_visit_records table
    op.drop_column('crm_sales_visit_records', 'recorder_id') 
    
    # Drop is_first_visit column from crm_sales_visit_records table
    op.drop_column('crm_sales_visit_records', 'is_first_visit') 

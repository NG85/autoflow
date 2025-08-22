"""add subject to visit record

Revision ID: 9f1430bc28cf
Revises: 6a22c062ecee
Create Date: 2025-08-21 11:48:55.594703

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9f1430bc28cf'
down_revision = '6a22c062ecee'
branch_labels = None
depends_on = None


def upgrade():
    # Add subject column
    op.add_column('crm_sales_visit_records', sa.Column('subject', sa.String(50), nullable=True))

    # Add followup_content column
    op.add_column('crm_sales_visit_records', sa.Column('followup_content', sa.Text(), nullable=True))

    # Rename AI generated fields to Multi-language fields
    op.alter_column('crm_sales_visit_records', 'followup_quality_level', 
                   new_column_name='followup_quality_level_zh',
                   existing_type=sa.String(100),
                   existing_nullable=True)
    op.alter_column('crm_sales_visit_records', 'followup_quality_reason', 
                   new_column_name='followup_quality_reason_zh',
                   existing_type=sa.Text(),
                   existing_nullable=True)
    op.alter_column('crm_sales_visit_records', 'next_steps_quality_level', 
                   new_column_name='next_steps_quality_level_zh',
                   existing_type=sa.String(100),
                   existing_nullable=True)
    op.alter_column('crm_sales_visit_records', 'next_steps_quality_reason', 
                   new_column_name='next_steps_quality_reason_zh',
                   existing_type=sa.Text(),
                   existing_nullable=True)

    # Add Multi-language fields
    op.add_column('crm_sales_visit_records', sa.Column('followup_record_zh', sa.Text(), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('next_steps_zh', sa.Text(), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('followup_record_en', sa.Text(), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('followup_quality_level_en', sa.String(100), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('followup_quality_reason_en', sa.Text(), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('next_steps_en', sa.Text(), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('next_steps_quality_level_en', sa.String(100), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('next_steps_quality_reason_en', sa.Text(), nullable=True))

    # Add index for subject column for better query performance
    op.create_index(
        "idx_subject",
        "crm_sales_visit_records",
        ["subject"],
        unique=False,
    )


def downgrade():
    # Drop index for subject column
    op.drop_index("idx_subject", table_name="crm_sales_visit_records")

    # Drop Multi-language fields
    op.drop_column('crm_sales_visit_records', 'next_steps_quality_reason_en')
    op.drop_column('crm_sales_visit_records', 'next_steps_quality_level_en')
    op.drop_column('crm_sales_visit_records', 'next_steps_en')
    op.drop_column('crm_sales_visit_records', 'followup_quality_reason_en')
    op.drop_column('crm_sales_visit_records', 'followup_quality_level_en')
    op.drop_column('crm_sales_visit_records', 'followup_record_en')
    op.drop_column('crm_sales_visit_records', 'next_steps_zh')
    op.drop_column('crm_sales_visit_records', 'followup_record_zh')

    # Rename Multi-language fields back to original AI generated fields
    op.alter_column('crm_sales_visit_records', 'next_steps_quality_reason_zh', 
                   new_column_name='next_steps_quality_reason',
                   existing_type=sa.Text(),
                   existing_nullable=True)
    op.alter_column('crm_sales_visit_records', 'next_steps_quality_level_zh', 
                   new_column_name='next_steps_quality_level',
                   existing_type=sa.String(100),
                   existing_nullable=True)
    op.alter_column('crm_sales_visit_records', 'followup_quality_reason_zh', 
                   new_column_name='followup_quality_reason',
                   existing_type=sa.Text(),
                   existing_nullable=True)
    op.alter_column('crm_sales_visit_records', 'followup_quality_level_zh', 
                   new_column_name='followup_quality_level',
                   existing_type=sa.String(100),
                   existing_nullable=True)
    
    # Drop followup_content column
    op.drop_column('crm_sales_visit_records', 'followup_content')
    
    # Drop subject column
    op.drop_column('crm_sales_visit_records', 'subject')
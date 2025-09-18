"""add_visit_start_end_time

Revision ID: bd55b5ef14b4
Revises: d32507dc9801
Create Date: 2025-09-17 17:30:46.363803

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bd55b5ef14b4'
down_revision = 'd32507dc9801'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('crm_sales_visit_records', sa.Column('visit_start_time', sa.String(19), nullable=True))
    op.add_column('crm_sales_visit_records', sa.Column('visit_end_time', sa.String(19), nullable=True))

def downgrade():
    op.drop_column('crm_sales_visit_records', 'visit_start_time')
    op.drop_column('crm_sales_visit_records', 'visit_end_time')
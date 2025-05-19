"""add_file_id_to_document

Revision ID: b5cc0f034947
Revises: 8c5b4a7a7945
Create Date: 2025-05-16 14:28:59.219487

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5cc0f034947'
down_revision = '8c5b4a7a7945'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("file_id", sa.Integer(), nullable=True))
    op.create_foreign_key(op.f("fk_documents_on_file_id"), "documents", "uploads", ["file_id"], ["id"])
    op.create_index(op.f("ix_documents_file_id"), "documents", ["file_id"], unique=False)
    
    op.execute("ALTER TABLE uploads ADD COLUMN category VARCHAR(100) GENERATED ALWAYS AS (JSON_UNQUOTE(meta->'$.category')) VIRTUAL")
    op.create_index(op.f("ix_uploads_category"), "uploads", ["category"], unique=False)

def downgrade():
    op.drop_index(op.f("ix_uploads_category"), table_name="uploads")
    op.drop_column("uploads", "category")
    
    op.drop_index(op.f("ix_documents_file_id"), table_name="documents")
    op.drop_constraint(op.f("fk_documents_on_file_id"), "documents", type_="foreignkey")
    op.drop_column("documents", "file_id")
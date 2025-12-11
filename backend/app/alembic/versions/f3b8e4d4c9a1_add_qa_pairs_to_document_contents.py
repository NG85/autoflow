"""add_qa_pairs_to_document_contents

Revision ID: f3b8e4d4c9a1
Revises: 0ea41d2c0ce7
Create Date: 2025-12-11 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f3b8e4d4c9a1"
down_revision = "0ea41d2c0ce7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 添加文档问答对字段
    op.add_column(
        "document_contents",
        sa.Column("qa_pairs", sa.JSON(), nullable=True, comment="从文档内容中抽取的问答对列表"),
    )
    op.add_column(
        "document_contents",
        sa.Column(
            "qa_extract_status",
            sa.String(20),
            nullable=True,
            comment="问答对抽取状态: success, failed",
        ),
    )

    # 可选索引，便于按抽取状态筛选
    op.create_index(
        "idx_qa_extract_status",
        "document_contents",
        ["qa_extract_status"],
        unique=False,
    )


def downgrade() -> None:
    # 删除索引
    op.drop_index("idx_qa_extract_status", table_name="document_contents")

    # 删除字段
    op.drop_column("document_contents", "qa_extract_status")
    op.drop_column("document_contents", "qa_pairs")


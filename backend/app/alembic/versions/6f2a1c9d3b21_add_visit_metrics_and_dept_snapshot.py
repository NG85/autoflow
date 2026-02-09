"""add recorder department snapshot to visit records

Revision ID: 6f2a1c9d3b21
Revises: 3001941b30f8
Create Date: 2026-02-05

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6f2a1c9d3b21"
down_revision: str = "3001941b30f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------
    # 1) recorder 部门快照字段（写入时固化）
    # ------------------------------------------------------------
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("recorder_department_id", sa.String(length=100), nullable=True, comment="记录人部门ID（快照）"),
    )
    op.add_column(
        "crm_sales_visit_records",
        sa.Column("recorder_department_name", sa.String(length=255), nullable=True, comment="记录人部门名称（快照）"),
    )
    op.create_index(
        "idx_visit_record_recorder_department_id",
        "crm_sales_visit_records",
        ["recorder_department_id"],
        unique=False,
    )

    # ------------------------------------------------------------
    # 2) 一次性回填历史快照数据
    # ------------------------------------------------------------
    # 说明：
    # - recorder_id 在 crm_sales_visit_records 中为 32 位 hex（无短横线）
    # - user_department_relation.user_id 为 36 位 UUID（带短横线）
    # - 本回填使用“当前主部门映射”作为历史初始化快照（无法还原历史组织结构变化）
    op.execute(
        sa.text(
            """
            WITH primary_dept AS (
              SELECT user_id, department_id
              FROM (
                SELECT
                  user_id,
                  department_id,
                  ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY is_primary DESC, id ASC) AS rn
                FROM user_department_relation
                WHERE is_active = 1
                  AND user_id IS NOT NULL AND user_id <> ''
                  AND department_id IS NOT NULL AND department_id <> ''
              ) t
              WHERE rn = 1
            )
            UPDATE crm_sales_visit_records v
            JOIN primary_dept d
              ON d.user_id = LOWER(CONCAT(
                SUBSTR(CAST(v.recorder_id AS CHAR), 1, 8), '-',
                SUBSTR(CAST(v.recorder_id AS CHAR), 9, 4), '-',
                SUBSTR(CAST(v.recorder_id AS CHAR), 13, 4), '-',
                SUBSTR(CAST(v.recorder_id AS CHAR), 17, 4), '-',
                SUBSTR(CAST(v.recorder_id AS CHAR), 21, 12)
              ))
            LEFT JOIN department_mirror m
              ON m.unique_id = d.department_id AND m.is_active = 1
            SET
              v.recorder_department_id = d.department_id,
              v.recorder_department_name = COALESCE(m.department_name, '')
            WHERE v.recorder_id IS NOT NULL
              AND (v.recorder_department_id IS NULL OR v.recorder_department_id = '')
            """
        )
    )


def downgrade() -> None:
    op.drop_index("idx_visit_record_recorder_department_id", table_name="crm_sales_visit_records")
    op.drop_column("crm_sales_visit_records", "recorder_department_name")
    op.drop_column("crm_sales_visit_records", "recorder_department_id")


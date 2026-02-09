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
    #
    # 注意：历史数据量大时，一次性 UPDATE 可能导致长事务/锁过久/迁移超时。
    # 这里按主键 id 游标分批回填，并在 autocommit block 中让每批独立提交。
    bind = op.get_bind()
    batch_size = 500
    last_id = 0

    # 预计算“主部门映射”到中间表，避免每批 UPDATE 都重复窗口函数，触发 TiDB 单条 SQL 内存配额限制。
    # - 先写入 is_primary=1 的映射（若同一 user_id 多条，则取最小 id）
    # - 再补齐没有主部门标记的用户（取最小 id），INSERT IGNORE 避免覆盖已有映射
    tmp_primary_dept_table = "tmp_primary_dept_6f2a1c9d3b21"
    bind.execute(sa.text(f"DROP TABLE IF EXISTS {tmp_primary_dept_table}"))
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE {tmp_primary_dept_table} (
              user_id VARCHAR(36) NOT NULL PRIMARY KEY,
              department_id VARCHAR(100) NOT NULL
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {tmp_primary_dept_table} (user_id, department_id)
            SELECT LOWER(u.user_id) AS user_id, u.department_id
            FROM user_department_relation u
            JOIN (
              SELECT user_id, MIN(id) AS min_id
              FROM user_department_relation
              WHERE is_active = 1
                AND is_primary = 1
                AND user_id IS NOT NULL AND user_id <> ''
                AND department_id IS NOT NULL AND department_id <> ''
              GROUP BY user_id
            ) x ON x.user_id = u.user_id AND x.min_id = u.id
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            INSERT IGNORE INTO {tmp_primary_dept_table} (user_id, department_id)
            SELECT LOWER(u.user_id) AS user_id, u.department_id
            FROM user_department_relation u
            JOIN (
              SELECT user_id, MIN(id) AS min_id
              FROM user_department_relation
              WHERE is_active = 1
                AND user_id IS NOT NULL AND user_id <> ''
                AND department_id IS NOT NULL AND department_id <> ''
              GROUP BY user_id
            ) x ON x.user_id = u.user_id AND x.min_id = u.id
            """
        )
    )

    select_ids_stmt = sa.text(
        """
        SELECT id
        FROM crm_sales_visit_records
        WHERE id > :last_id
          AND recorder_id IS NOT NULL
          AND (recorder_department_id IS NULL OR recorder_department_id = '')
        ORDER BY id
        LIMIT :limit
        """
    )

    update_batch_stmt = (
        sa.text(
            """
            UPDATE crm_sales_visit_records v
            JOIN tmp_primary_dept_6f2a1c9d3b21 d
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
            WHERE v.id IN :ids
              AND v.recorder_id IS NOT NULL
              AND (v.recorder_department_id IS NULL OR v.recorder_department_id = '')
            """
        ).bindparams(sa.bindparam("ids", expanding=True))
    )

    # 每批独立提交，避免大事务与长时间持锁
    # 说明：autocommit_block 是 MigrationContext 的方法，不是 alembic.context 模块的方法
    try:
        with op.get_context().autocommit_block():
            while True:
                rows = bind.execute(select_ids_stmt, {"last_id": last_id, "limit": batch_size}).fetchall()
                if not rows:
                    break
                ids = [r[0] for r in rows]
                last_id = ids[-1]
                bind.execute(update_batch_stmt, {"ids": ids})
    finally:
        # 尽量清理中间表；即使迁移失败也不影响后续重试
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {tmp_primary_dept_table}"))


def downgrade() -> None:
    op.drop_index("idx_visit_record_recorder_department_id", table_name="crm_sales_visit_records")
    op.drop_column("crm_sales_visit_records", "recorder_department_name")
    op.drop_column("crm_sales_visit_records", "recorder_department_id")


# 数据库方言运维手册（TiDB / PostgreSQL）

一个运行环境只使用一种数据库引擎。通过 `DATABASE_DIALECT` 选择，**不做双写、不做跨库事务**。

功能与实现规格见仓库根目录：

- `fsd/2026-11-17-database-dialect.md`
- `tsd/2026-11-17-database-dialect.md`

本文只写运维操作。

## 1. 配置

| 变量 | 说明 |
|------|------|
| `DATABASE_DIALECT` | `tidb`（默认）或 `postgresql` |
| `TIDB_*` | `DATABASE_DIALECT=tidb` 时必填（至少 `TIDB_DATABASE`） |
| `PG_*` | `DATABASE_DIALECT=postgresql` 时必填（至少 `PG_USER`、`PG_DATABASE`） |
| `PG_SSLMODE` | 默认 `prefer` |

示例见仓库根目录 / `backend` 的 `.env.example`。改完 `.env` 后须**重启进程**。若 shell 已 `export` 过旧的 `DATABASE_DIALECT` 或连接变量，会盖过 `.env`，启动前先 `unset`。

### 连接驱动

| 方言 | 常见 URL |
|------|----------|
| tidb | `mysql+pymysql://…`（Alembic / 同步）；异步栈按现有 TiDB 配置 |
| postgresql | `postgresql+psycopg://…` |

## 2. 迁移树

```
backend/app/alembic/versions/
  tidb/          # 历史增量（生产 TiDB 链）
  postgresql/    # 新环境基线 + 后续 PG 修订
  active -> …    # 运行时按 DATABASE_DIALECT 生成的软链，勿提交
```

- 加载 Settings / 导入 `app.alembic` 时会把 `active` 指到对应目录。
- TiDB：继续在 `tidb/` 上增量。
- PostgreSQL：**不重放** TiDB 历史；新库执行 `postgresql/` 基线及之后修订（含 `b7e4a91c2d10` 对 `users` 的外键）。

```bash
cd backend
uv run alembic current
uv run alembic upgrade head
# 或 make migrate
```

## 3. 与 OAuth 的部署顺序（PostgreSQL 新库）

`users` 由 **aptsell-oauth** 维护。PG 新环境：

1. 先跑 **oauth** 的 PostgreSQL 迁移（建 `users` 等，含权限种子）
2. 再跑 **autoflow** 的 PostgreSQL 迁移（建本服务表，并加对 `users` 的外键）

缺 `users` 时本服务外键修订会失败。TiDB 共享库上版本表独立：oauth 用 `oauth_alembic_version`，autoflow 用 `alembic_version`。

oauth 侧操作见 aptsell-oauth 仓库 `docs/database-dialect.md`。

## 4. 本服务表 vs 外部表

`app/alembic/external_tables.py` 中的表由**其他服务**建表，本服务只映射，Alembic **不创建**。

典型外部表：`users`、`oauth_accounts`、`user_profiles`、部门镜像，以及多数 `crm_*` / `crm_review_*`。

- 切到 PostgreSQL 后，若 CRM 等尚未在同一库建表，依赖这些表的业务会失败（可观测，不伪装成功）。
- 向量 / 图谱、聊天、知识库、跟进自有表等由本服务基线创建的，可在 PG 上独立验证。

## 5. 向量与知识图谱

| 能力 | TiDB | PostgreSQL |
|------|------|------------|
| 向量类型 | TiDB VECTOR / 既有适配 | `pgvector`（基线 `CREATE EXTENSION vector`） |
| HNSW | 既有 VectorAdaptor 约定 | `USING hnsw (... vector_cosine_ops)` |
| 动态表 | `chunks_{kb}` / `entities_{kb}` / `relationships_{kb}` 运行时创建 | 同左 |

入口：`app/models/vector_type.py`、`app/rag/vector_index.py`、graph/vector store。

## 6. 方言 SQL

应用层 MySQL 专有写法集中在 `app/core/dialect_sql.py`（如聚合、`ON DUPLICATE`/`ON CONFLICT`、部分 JSON 判断）。新增原生 SQL 必须按方言分支或走该模块，禁止写死仅 MySQL 可跑的语句。

## 7. 验收建议

TiDB 回归：

```bash
curl -s http://127.0.0.1:3000/api/v1/healthz
cd backend && uv run alembic current
```

PostgreSQL 新库冒烟：确认 `DATABASE_DIALECT=postgresql`、oauth 已迁完、`alembic upgrade head`、健康检查通过；有 KB 时再验 embedding / HNSW 与检索。

## 8. 相关代码

- `app/core/config.py` — `DatabaseDialect`、URI、迁移树激活
- `app/alembic/env.py` / `app/alembic/__init__.py`
- `app/alembic/external_tables.py`
- `app/core/dialect_sql.py`

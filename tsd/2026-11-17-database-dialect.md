# 技术规格说明书：TiDB / PostgreSQL 双方言实现

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-17 |
| 变更窗口 | 2026-11-17 |
| 前置说明书 | `tsd/2026-10-10-autoflow-baseline.md` |
| 对应功能规格 | `fsd/2026-11-17-database-dialect.md` |
| 关联代码入口 | `backend/app/core/db.py`；`backend/app/core/config.py`；`backend/app/alembic/`；`backend/app/models/`；`backend/app/rag/` |

## 1. 配置与连接

`Settings.DATABASE_DIALECT`：`tidb`（默认）或 `postgresql`。

| 方言 | 连接字段 | URL 形态 |
| --- | --- | --- |
| tidb | `TIDB_HOST/PORT/USER/PASSWORD/DATABASE` | `mysql+pymysql://...` |
| postgresql | `PG_HOST/PORT/USER/PASSWORD/DATABASE` | `postgresql+psycopg://...` |

`SQLALCHEMY_DATABASE_URI` 按方言拼装。启动时打日志；`/health`（或等价健康接口）回显当前方言。进程已 export 的 `DATABASE_DIALECT` 优先于 `.env`。

## 2. Alembic 双树

| 路径 | 用途 |
| --- | --- |
| `backend/app/alembic/versions/tidb/` | TiDB 历史增量 |
| `backend/app/alembic/versions/postgresql/` | PG：`c6821ddd604a` 基线 + 后续修订 |
| `backend/app/alembic/versions/active` | 指向当前方言目录的符号链接 |

`app.alembic.__init__.ensure_alembic_version_path()`（及 env 钩子）在迁移前按 `DATABASE_DIALECT` 刷新 `active`。PostgreSQL 不重放 TiDB 版本文件。

表归属：

- **OWNED**：本服务迁移创建/变更（聊天、KB 元数据、跟进自有表等）
- **EXTERNAL**：仅模型/元数据引用，迁移不建表（多数 CRM、`users` 等）

清单与判定：`backend/app/alembic/external_tables.py`（及注册到 env 的 include 逻辑）。

PostgreSQL 外键修订示例：`.../postgresql/2026_11_17_1455-b7e4a91c2d10_add_users_foreign_keys.py`（指向 `users`；含孤儿清理）。须在 oauth 已创建 `users` 之后执行。

## 3. SQL 与类型适配

业务与 RAG 中方言相关 SQL 经统一入口分流（如 `dialect_sql` / 等价封装），禁止散落裸 `SHOW TABLES` 等仅 MySQL 语法到 PG 路径。

| 能力 | TiDB | PostgreSQL |
| --- | --- | --- |
| 向量列 | TiDB VECTOR 等既有类型 | `pgvector` VECTOR |
| 近似索引 | 既有 HNSW/向量索引约定 | HNSW（余弦距离） |
| UUID 存取 | 兼容 32 位无连字符与 36 位标准串 | 同左；工具层双格式，不假设仅 CHAR(32) |

动态 KB 表（chunks / entities / relationships）创建与检索路径按方言选择 DDL 与距离表达式；对外相似度语义保持余弦一致。

## 4. 模型与运行时

SQLAlchemy 模型在两边共用一套 Python 声明；列类型与默认值经 dialect 分支（如 `vector_type`）解析。图谱与向量写入走同一业务 API，底层 DDL/DML 随方言切换。

依赖共享 `users`：本服务不注册本地管理员覆盖 oauth；外键仅约束引用完整性。

## 5. 部署检查点

1. 确认 `DATABASE_DIALECT` 与连接串一致，无进程环境脏变量。
2. PG：oauth `alembic upgrade head` → autoflow `alembic upgrade head`。
3. `alembic current` 落在对应树 head；健康检查方言字段正确。
4. 抽样：自有表可写；依赖 EXTERNAL 的路径在表缺失时失败可观测。

## 6. 非目标

- CRM/review 等外部服务的 PostgreSQL 迁移实现
- 数据搬迁脚本与双写同步

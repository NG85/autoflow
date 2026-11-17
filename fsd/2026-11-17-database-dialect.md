# 功能规格说明书：TiDB / PostgreSQL 双方言运行

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-17 |
| 变更窗口 | 2026-11-17 |
| 前置说明书 | `fsd/2026-10-10-autoflow-baseline.md`（持久化与运行环境） |
| 关联服务 | backend；TiDB 或 PostgreSQL（二选一）；aptsell-oauth；CRM 等外部表归属服务 |
| 关联功能模块 | 配置与迁移、向量与知识图谱、CRM 自有表读写、鉴权依赖的 `users` |

本文件描述运维与调用方可感知的双方言能力。实现落点见 `tsd/2026-11-17-database-dialect.md`。历史说明书不改。

## 1. 这次交付什么

一个运行环境只连接一种数据库：`tidb` 或 `postgresql`。环境变量 `DATABASE_DIALECT` 决定引擎与迁移树。不做双写，不在一次请求里跨两种库。

默认仍是 `tidb`，现有 TiDB 部署不设该变量时行为与改前一致。切到 PostgreSQL 时，本服务自有业务表、向量与图谱索引可在该库上创建与读写；由其他服务维护的表（含多数 CRM 镜像与 `users`）须已在同一库就绪，否则依赖这些表的能力不可用。

## 2. 谁受影响

| 角色 | 变化 |
| --- | --- |
| 运维 | 按方言配置连接串，跑对应 Alembic 树；PostgreSQL 新库须先完成 oauth 迁移再跑本服务 |
| 终端用户 / 业务调用方 | API 路径与权限语义不变；库未备齐外部表时，相关业务失败，而不是静默改口径 |
| 知识库管理员 | 在对应方言库上入库、建向量/图谱索引；相似度与检索语义不变 |

## 3. 配置与迁移（可验收行为）

- `DATABASE_DIALECT=tidb`：使用 `TIDB_*`；迁移走 TiDB 历史增量链。
- `DATABASE_DIALECT=postgresql`：使用 `PG_*`；迁移走 PostgreSQL 基线及后续修订，不重放 TiDB 历史文件。
- 改方言或连接后必须重启进程。进程环境里若已导出旧的 `DATABASE_DIALECT`，会盖过 `.env`。

PostgreSQL 新环境顺序：

1. aptsell-oauth 升级到其 PostgreSQL head（创建 `users` 等）
2. 本服务升级到 PostgreSQL head（创建自有表，并建立指向 `users` 的外键）

## 4. 能力边界

本服务负责建表与迁移的数据：在选定方言上可读写，包括聊天、知识库元数据、跟进自有表、动态 KB 的 chunks/entities/relationships 及向量索引。

外部表（见技术规格中的清单）：本服务不创建；表不存在时相关查询或写入失败。登录与账号仍依赖 oauth 与共享 `users`。

## 5. 验收

| 项 | 标准 |
| --- | --- |
| TiDB 默认 | 未设 `DATABASE_DIALECT` 或设为 `tidb` 时，健康检查与既有业务可连上 TiDB |
| PostgreSQL 切换 | 设为 `postgresql` 且 `PG_*` 齐全时，健康检查连上 PG；`alembic current` 在 postgresql 树 head |
| 部署顺序 | PG 新库先 oauth 后 autoflow；缺 `users` 时本服务外键迁移失败并提示 |
| 向量图谱 | PG 上可为 KB 创建带 embedding 的动态表与 HNSW；检索距离语义与 TiDB 侧一致（余弦） |
| 外部表缺失 | CRM 等外部表未迁入时，依赖路径失败可观测，不伪装成功 |

## 6. 不在本次范围

- 把 CRM / review 等外部服务的表迁到 PostgreSQL（属对应服务）
- 双活双写或在线从 TiDB 搬数据到 PostgreSQL
- 修改对外 HTTP 路径或权限码语义

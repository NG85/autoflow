# user_sessions 归档 Runbook

## 迁移

| 项 | 值 |
|----|-----|
| Alembic revision | `f8e2a1b0c9d4` |
| 归档表 | `user_sessions_archive_20260522` |
| 活跃表 | `user_sessions`（空表，同 schema） |

## 执行（低峰）

```bash
cd backend
# 备份由 DBA 负责（TiDB/MySQL 全库或单表）
alembic upgrade f8e2a1b0c9d4
# 或升级到 head：alembic upgrade head
```

## 验证

```sql
SHOW TABLES LIKE 'user_sessions%';
SELECT COUNT(*) FROM user_sessions;                    -- 预期 0（归档后）
SELECT COUNT(*) FROM user_sessions_archive_20260522;   -- 历史行数
```

应用侧（归档后）：

```bash
RUN=1 BASE_URL=... EMAIL=... PASSWORD=... ./scripts/auth_migration_smoke.sh
```

## 回滚（DB）

```bash
alembic downgrade e7a3c1d92b40
```

将恢复：删除空 `user_sessions`，把归档表改回 `user_sessions`。

应用回滚（若需 session 鉴权）请按发布系统回滚到含 legacy session 解析的历史版本执行。

## 注意

- 生产登录已 `AUTH_LEGACY_LOGIN_WRITES_SESSION=false`，归档 **不影响** 在线用户。
- 勿在归档后依赖旧 `session` Cookie；仅 `oauth_access_token` + API Key。

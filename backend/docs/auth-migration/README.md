# Auth（autoflow → aptsell-oauth）

最终状态说明（生产已验收）。

## 总体结论

- 鉴权为 **oauth-only**（API Key → oauth Bearer/Cookie → 401）。
- `POST /api/v1/auth/login` 保留：校验 `users` 密码后通过 `session/issue` 下发 `oauth_access_token`。
- 应用层不再读取 `user_sessions` 做请求鉴权；`session` Cookie 单独携带无效。
- 注册可走 oauth（`OAUTH_REGISTER_ENABLED=true`）；oauth 直写 `users` 表。

## 当前线上行为

| 能力 | 行为 |
|------|------|
| 登录 | `POST /auth/login` → `oauth_access_token` Cookie |
| 鉴权 | 仅 `oauth_access_token` 或 `ta-...` API Key 通过 |
| 注册 | `POST /users/register` 优先 oauth，失败 fallback 本地 create |
| Bootstrap | `OAUTH_BOOTSTRAP_VIA_OAUTH=true` 时先 oauth 注册再提权 superuser |

## 关键配置

oauth 为稳定依赖；`config.py` 默认已开启 shadow 注册。部署时 **必须在 .env 配置**：

```env
OAUTH_BASE_URL=http://auth:8018   # 按集群实际服务地址
OAUTH_SESSION_ISSUE_SECRET=<与 oauth SESSION_ISSUE_SERVICE_SECRET 一致>
```

本地无 oauth 时可关闭：`AUTH_LEGACY_OAUTH_SHADOW_ENABLED=false`、`OAUTH_REGISTER_ENABLED=false`。

可选：`OAUTH_BOOTSTRAP_VIA_OAUTH=true`（新环境 bootstrap admin 走 oauth）。

## 文档

- `03-smoke-tests.md` — 冒烟/回归用例
- `register-via-oauth.md` — 注册与 bootstrap 说明
- `user-sessions-archive-runbook.md` — `user_sessions` 归档手册

## 写入口边界

1. `users` / `user_sessions`：仅 autoflow `app/auth`、bootstrap、`POST /users/register` 可写。
2. `user_profiles` / `oauth_accounts`：仅 aptsell-oauth 可写；autoflow 只读。
3. `api_keys`：仅 autoflow `ApiKeyManager` 可写。
4. RBAC / 组织：仅通过 `oauth_client` 调 oauth，不在 autoflow 复制权限写逻辑。

## 主键约定

全链路以 `users.id`（UUID）为系统用户主键。

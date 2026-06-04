# Auth 冒烟用例

环境变量：`BASE_URL`、`EMAIL`/`PASSWORD`、可选 `API_KEY`（`ta-...`）。

脚本：`backend/scripts/auth_migration_smoke.sh`（默认只打印 curl；`RUN=1` 执行）。

可选扩展：`SMOKE_REGISTER=1`、`SMOKE_OAUTH_BFF=1`、`SMOKE_LOGIN_SHADOW=1`。

---

## P0 — 必跑

| ID | 步骤 | 预期 |
|----|------|------|
| P0-1 | `GET {BASE}/api/v1/healthz` | 200 |
| P0-2 | `GET {BASE}/api/v1/healthz/oauth` | 200；`status` 为 `ok` 或 `degraded` |
| P0-3 | `POST {BASE}/api/v1/auth/login` | 204 + Cookie（含 `oauth_access_token`） |
| P0-4 | `GET {BASE}/api/v1/users/me`（带 Cookie） | 200 |
| P0-5 | `GET {BASE}/api/v1/me/menu-config` | 200 或业务空权限，非 5xx |
| P0-6 | `POST {BASE}/api/v1/auth/logout` | 成功 |
| P0-7 | `GET {BASE}/api/v1/users/me`（登出后） | 401 |

---

## P1 — CRM/聊天（有权限时）

| ID | 步骤 | 预期 |
|----|------|------|
| P1-1 | `GET {BASE}/api/v1/chats` | 200 |
| P1-2 | 任一读 CRM 接口 | 200 或 403，非 5xx |

---

## P2 — API Key

| ID | 步骤 | 预期 |
|----|------|------|
| P2-1 | `GET /users/me`，`Authorization: Bearer {API_KEY}` | 200 |
| P2-2 | 无 Cookie 无 Key | 401 |

---

## 鉴权收口

| ID | 步骤 | 预期 |
|----|------|------|
| A-1 | 仅 legacy `session` Cookie → `GET /users/me` | **401** |
| A-2 | 仅 `oauth_access_token` → `GET /users/me` | **200** |
| A-3 | `Authorization: Bearer ta-...` → `GET /users/me` | **200** |
| A-4 | `SMOKE_REGISTER=1`：`POST /users/register` | 200 + `via_oauth: true`（需 `OAUTH_REGISTER_ENABLED=true`） |

应用层不读 `user_sessions`；`UserSession` ORM 与归档表仍保留。

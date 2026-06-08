# 注册走 oauth

## 行为

`POST /api/v1/users/register` 优先调用 oauth `POST /oauth/v1/user/register`；失败时 fallback `UserRepository.create`。响应含 `via_oauth: true|false`。

oauth `register_by_user_id` / 飞书 `register-or-get` 使用 `create_system_user` 直写 `users` 表（与 autoflow 密码哈希兼容）。

## 配置

默认已开启 `OAUTH_REGISTER_ENABLED`；部署需配置 `OAUTH_BASE_URL` 与 `OAUTH_SESSION_ISSUE_SECRET`（见 `README.md`）。

```env
OAUTH_BOOTSTRAP_VIA_OAUTH=false   # 新环境 bootstrap admin 可选 true
```

## Bootstrap admin

`python bootstrap.py --email ... --password ...`

- `OAUTH_BOOTSTRAP_VIA_OAUTH=false`：本地 `create_user`，直接 superuser。
- `OAUTH_BOOTSTRAP_VIA_OAUTH=true`：oauth 注册 → 同步 `users` 密码 → 提权 superuser。

## 回滚

```env
OAUTH_REGISTER_ENABLED=false
OAUTH_BOOTSTRAP_VIA_OAUTH=false
```

已注册用户不受影响。

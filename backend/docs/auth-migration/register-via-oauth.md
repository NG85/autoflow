# 注册走 oauth

## 行为

`POST /api/v1/users/register` 优先调用 oauth `POST /oauth/v1/user/register`；失败时 fallback `UserRepository.create`。响应含 `via_oauth: true|false`。

oauth `register_by_user_id` / 飞书 `register-or-get` 使用 `create_system_user` 直写 `users` 表（与 autoflow 密码哈希兼容）。

## 配置

默认已开启 `OAUTH_REGISTER_ENABLED`；部署需配置 `OAUTH_BASE_URL` 与 `OAUTH_SESSION_ISSUE_SECRET`（见 `README.md`）。

## Bootstrap

`python bootstrap.py --email ... --password ...`

- admin 与 sia 都先 oauth 注册；oauth 失败时 fallback 本地 `create_user`。
- admin 同步密码并提权 superuser；sia 保持非超管并签发 API Key。

## 回滚

```env
OAUTH_REGISTER_ENABLED=false
```

已注册用户不受影响。

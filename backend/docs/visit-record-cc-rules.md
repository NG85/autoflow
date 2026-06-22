# 拜访记录卡片抄送规则（notification_cc_rules）

设计说明 v1。描述拜访记录卡片推送中「配置化抄送」的目标、数据模型、收集算法与演进路径。

## 背景

拜访记录卡片推送入口：`push_visit_record_message` → `PlatformNotificationService.send_visit_record_notification`。

当前抄送侧主要依赖 OAuth 权限 `visit_record:card:receive`：凡具备该权限的用户，会在**每一次**拜访推送时收到 leader 版卡片。该机制适合「公司级旁观」，但无法表达：

- 销售 A 的拜访抄送给张三
- 销售 B 的拜访抄送给李四

需要引入可配置的抄送规则表，在主送与现有管理层推送之外，按录入人（销售）差异化追加抄送人。

## 术语

| 术语 | 含义 |
|------|------|
| 主送 | 录入人、内部协同人；使用 recorder 版卡片模板 |
| 管理层 | 汇报链上级（及 department_review 群）；使用 leader 版卡片模板 |
| 抄送 | 配置规则 +（过渡期）OAuth 全局权限用户；使用 leader 版卡片模板 |
| 录入人 / recorder | 拜访记录的 `recorder_id` / `recorder`，对应 `user_profiles` |
| 资格（permission） | 用户是否**允许**接收拜访抄送（`visit_record:card:receive`） |
| 路由（rule） | 某次拜访**应该**抄送给谁 |

> 代码中不存在 IM 意义上的 To/CC 字段；「主送 / 抄送」通过 recipient `type` 与卡片模板区分，每人单独发一张卡片。

## 目标

1. 支持按**销售 user_id** 配置抄送人列表。
2. 多条规则同时命中时，**合并**抄送人；同一接收人去重。
3. 与现有主送、汇报链、协同人、部门群推送**叠加**，不破坏现有行为。
4. 抄送收集与汇报链查询**解耦**（汇报链为空时仍可按规则抄送）。
5. 为长期演进预留：`visit_record:card:receive` 从「全局路由源」收敛为「资格 gate」。

## 非目标（v1）

- 管理后台 UI（可先通过 SQL / 脚本 / 内部 API 维护规则）。
- 按部门、按商机等复杂匹配（表结构预留，v1 仅实现 `user`）。
- 修改 recorder / leader 版卡片模板内容。
- 替代汇报链上级推送逻辑。

---

## 现有推送分层（不变）

```
send_visit_record_notification
  └─ _collect_visit_record_recipients_and_groups
       ├─ get_recipients_for_recorder          # recorder + leader + executive_admin(OAuth)
       ├─ _get_collaborative_participants_recipients
       ├─ [新增] _resolve_visit_record_cc_recipients
       ├─ department_review 群 / visit_record 简报群
       └─ department_review 命中时：个人列表移除 leader，改推群
  └─ _send_visit_record_to_individual_recipients   # 按 type 选模板，open_id 去重
  └─ _send_visit_record_to_review_groups
  └─ _send_visit_record_to_brief_groups
```

### 现有 recipient type 与模板

| type | 模板 | 说明 |
|------|------|------|
| `recorder` | recorder 版 | 录入人 |
| `collaborative_participant` | recorder 版 | 内部协同人（需有 `ask_id`） |
| `leader` | leader 版 | OAuth 汇报链管理层（`max_levels=1`：本部门 leader；无则 fallback 上级部门主管） |
| `executive_admin` | leader 版 | `visit_record:card:receive` 权限用户（**过渡期全局路由源**） |
| `configured_cc` | leader 版 | **[新增]** 配置表解析出的抄送人 |

### 推送优先级（同 open_id 只推一张）

数值越小优先级越高，决定保留哪个 type 的模板：

```
recorder(0) > leader(1) > configured_cc(2) > executive_admin(3) > collaborative_participant(4)
```

---

## 数据模型

### 表：`notification_cc_rules`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT PK | 主键 |
| `event_type` | VARCHAR(64) | 事件类型；v1 固定 `visit_record_card` |
| `scope_type` | VARCHAR(32) | 匹配维度；v1 固定 `user`；预留 `global`、`department` |
| `scope_user_id` | UUID NULL | `scope_type=user` 时：录入人的 `users.id` |
| `scope_department_id` | VARCHAR NULL | 预留 |
| `priority` | INT DEFAULT 0 | **不用于过滤**；管理端排序、日志、后续扩展 |
| `enabled` | BOOLEAN DEFAULT TRUE | 是否启用 |
| `description` | VARCHAR(512) NULL | 备注 |
| `created_at` / `updated_at` | TIMESTAMP | 审计 |

索引建议：

- `(event_type, scope_type, scope_user_id, enabled)` — 按录入人查规则
- `(event_type, scope_type, enabled)` — 预留 global 规则

### 表：`notification_cc_rule_recipients`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT PK | 主键 |
| `rule_id` | BIGINT FK | → `notification_cc_rules.id` |
| `user_id` | UUID | 抄送人 `users.id` |
| `created_at` | TIMESTAMP | 审计 |

唯一约束：`(rule_id, user_id)`。

---

## 抄送人收集算法（写死）

以下顺序在 `_collect_visit_record_recipients_and_groups` 中执行，**不得随意调整**。

### Step 0：解析录入人 user_id

从 `recorder_id` / `recorder_name` 查 `user_profiles`，得到 `recorder_user_id`（`user_profiles.user_id`）。  
无法解析时：跳过配置抄送（主送仍按现有逻辑尝试）。

### Step 1：查命中规则

```sql
SELECT * FROM notification_cc_rules
WHERE event_type = 'visit_record_card'
  AND enabled = TRUE
  AND scope_type = 'user'
  AND scope_user_id = :recorder_user_id
```

（后续 `global` 规则：额外 `OR scope_type = 'global'`，与 user 规则一并参与合并。）

### Step 2：合并规则内抄送人

- 遍历所有命中规则（可按 `priority` DESC 排序，**仅影响遍历顺序，不影响最终名单**）。
- 收集各规则 `notification_cc_rule_recipients.user_id`。
- **按 `user_id` 去重**，得到 `configured_cc_user_ids`。

### Step 3：并入 OAuth 全局名单（固定叠加）

`configured_cc_user_ids ∪ oauth_permission_user_ids`，再按 `user_id` / `open_id` 去重。

`oauth_permission_user_ids` 来源：`oauth_client.get_users_by_permission("visit_record:card:receive")`。

> **过渡期说明**：OAuth 权限用户列表充当「全局抄送路由源」。这是**兼容手段，非终态**（见「演进路径」）。无站点开关，代码固定为叠加模式。

### Step 4：解析 profile 并构造 recipient

对配置表抄送人：

1. **批量**查 `user_profiles`（`user_id` → `open_id`、`platform`）；无 profile 或无 `open_id` 的跳过并打 warn 日志。
2. 暂不强制校验 `visit_record:card:receive` 资格（迁移迭代时可在代码内开启）。

### Step 5：构造 recipient

每个通过 Step 4 的 user 追加到 `recipients_by_platform`：

```python
{
    "open_id": ...,
    "name": ...,
    "type": "configured_cc",   # 来自配置表
    # 或 "executive_admin"      # 来自 OAuth 名单（过渡期保留原 type 便于日志区分）
    "department": ...,
    "receive_id_type": "open_id",
    "platform": ...,
}
```

### Step 6：与主送 / leader 合并后推送

`_send_visit_record_to_individual_recipients` 内：

- 按 `_VISIT_RECORD_RECIPIENT_TYPE_PRIORITY` 排序；
- 以 `(platform, open_id)` 为键去重，每人只发一张卡片。

---

## 与现有逻辑的关系

### 叠加，不替代（短期）

| 来源 | v1 角色 |
|------|---------|
| `notification_cc_rules` | 按销售 user_id 的差异化抄送（主路径） |
| `visit_record:card:receive` 用户列表 | 全局抄送（**过渡期兼容**） |
| 汇报链 `leader` | 管理层，非配置抄送；`max_levels=1` 时 OAuth 无本部门 leader 会 fallback 上级部门主管 |

### department_review 群

部门配置了 `department_review` 群时：

- 个人列表中的 `leader`、`executive_admin`、`cc_scope=global` 的 `configured_cc` 被移除；
- leader 版卡片改推 review 群（管理层与 global 抄送受众一般在群内）；
- `cc_scope=user` 的 `configured_cc`（按销售配置的 personal 抄送）仍推送给个人；
- 个人推送始终保留 `recorder` 与 `collaborative_participant`。

### 汇报链 early-return 修复

现状：`get_recipients_for_recorder` 在汇报链为空时提前 `return`，导致 OAuth 权限用户也不会加入。

改造（与本需求一并做）：

- 将「添加 OAuth 权限用户」从「汇报链非空」分支中拆出；
- 配置抄送 entirely 在 `_collect_visit_record_recipients_and_groups` 独立步骤完成，不依赖汇报链结果。

---

## 演进路径

### 当前（v1）

```
实际抄送名单 = 配置表规则抄送人 ∪ OAuth permission 用户列表（固定叠加、去重）
资格校验：暂不强制
```

### 终态（迭代迁移）

```
路由：仅 notification_cc_rules（含 scope_type=global 的全局规则）
资格：visit_record:card:receive 仅作 gate（有没有资格收）
实现：代码内收敛 OAuth 名单并入逻辑，无需站点开关
```

### 迁移步骤

1. 为客户创建一条 `scope_type=global` 规则，recipients = 当前 OAuth permission 用户。
2. 验证推送名单与过渡期一致。
3. 代码侧停止将 OAuth permission 用户列表直接并入抄送（或改为仅资格 gate）。
4. 文档与运维手册标注：permission 不再维护抄送名单，仅维护「可接收」资格。

---

## 代码改动范围（实现清单）

| 模块 | 改动 |
|------|------|
| `app/models/notification_cc_rule.py` | 新模型 |
| `app/alembic/versions/...` | 建表 migration |
| `app/repositories/notification_cc_rule.py` | 按 recorder_user_id 查规则 + recipients |
| `app/services/visit_record_cc_resolver.py` | 收集算法 Step 0–5（可独立单测） |
| `app/services/platform_notification_service.py` | 接入 resolver；修复汇报链 early-return；新增 `configured_cc` type 与 priority |
| `app/api/routes/...`（可选二期） | 规则 CRUD 内部 API |

---

## 示例

### 配置

| rule_id | scope_type | scope_user_id | recipients |
|---------|------------|---------------|------------|
| 1 | user | 销售-A 的 user_id | [张三, 李四] |
| 2 | user | 销售-A 的 user_id | [李四, 王五] |
| 3 | user | 销售-B 的 user_id | [赵六] |

OAuth permission 用户（全局）：[高管-陈总]

### 销售 A 录入拜访

1. 规则 1 + 2 命中 → 合并 → [张三, 李四, 王五]
2. 并入 OAuth → [张三, 李四, 王五, 高管-陈总]
3. 若李四同时是汇报链 leader → 最终只推一张 leader 版（leader 优先级高于 configured_cc）
4. 录入人本人 → recorder 版

### 销售 C 录入拜访（无规则）

1. 配置表无命中 → []
2. 并入 OAuth → [高管-陈总]
3. 与现状一致

---

## 测试要点

1. **多规则合并**：同 recorder 两条规则，recipient 并集且 user_id 去重。
2. **与 OAuth 叠加**：`merge` 模式下名单为并集；`rules_only` / `oauth_only` 互斥。
3. **open_id 去重**：同一人既是 leader 又是 configured_cc，只推一张，模板取高优先级 type。
4. **汇报链为空**：仍有配置抄送 / OAuth 抄送（验证 early-return 修复）。
5. **无 profile / 无 open_id**：跳过该抄送人，不影响其他人。
6. **department_review 群**：leader / global 抄送走群；`cc_scope=user` 的 configured_cc 仍走个人。

---

## 后续扩展（不在 v1）

| 扩展 | 说明 |
|------|------|
| `scope_type=global` | 终态替代 OAuth 全局名单 |
| `scope_type=department` | 按录入人部门匹配，`include_children` 可参考 `department_group_chats` |
| 管理 API / 后台页 | 规则 CRUD、命中预览（输入 recorder → 展示最终抄送名单） |
| 推送审计表 | 记录 rule_id、实际推送 open_id，便于排障 |

---

## 决策记录

| 决策 | 结论 | 日期 |
|------|------|------|
| 新规则 vs OAuth 权限 | 短期叠加去重；长期权限仅作资格 | — |
| 多规则命中 | 合并抄送人，不按 priority 取单条 | — |
| 同接收人去重 | user_id（规则层）+ open_id（推送层） | — |
| 匹配维度 v1 | `scope_type=user`，以录入人 `user_id` 为主 | — |
| OAuth 全局名单 | 过渡期路由源，文档标注非终态 | — |

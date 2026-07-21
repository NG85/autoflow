# 拜访记录卡片抄送规则（notification_cc_rules）

描述拜访记录卡片推送中「配置化抄送」的目标、数据模型与收集算法。

## 背景

拜访记录卡片推送入口：`push_visit_record_message` → `PlatformNotificationService.send_visit_record_notification`。

抄送名单仅来自 `notification_cc_rules`：

- `scope_type=user`：按录入人差异化抄送
- `scope_type=department`：按录入人主部门匹配
- `scope_type=global`：公司级旁观（每次拜访都抄送）

OAuth 权限 `visit_record:card:receive`（与 `notification:follow_up_card:receive` 等同）仅作**资格 gate**，不再作为全局路由源。

## 术语

| 术语 | 含义 |
|------|------|
| 主送 | 录入人、内部协同人；使用 recorder 版卡片模板 |
| 管理层 | 汇报链上级（及 department_review 群）；使用 leader 版卡片模板 |
| 抄送 | `notification_cc_rules`（含 global / department）；使用 leader 版卡片模板 |
| 录入人 / recorder | 拜访记录的 `recorder_id` / `recorder`，对应 `user_profiles` |
| 资格（permission） | 用户是否**允许**接收拜访抄送（`visit_record:card:receive` / `notification:follow_up_card:receive`） |
| 路由（rule） | 某次拜访**应该**抄送给谁 |

> 代码中不存在 IM 意义上的 To/CC 字段；「主送 / 抄送」通过 recipient `type` 与卡片模板区分，每人单独发一张卡片。

## 目标

1. 支持按**销售 user_id**、**部门 department_id**、**全局**配置抄送人列表。
2. 多条规则同时命中时，**合并**抄送人；同一接收人去重。
3. 与现有主送、汇报链、协同人、部门群推送**叠加**，不破坏现有行为。
4. 抄送收集与汇报链查询**解耦**（汇报链为空时仍可按规则抄送）。
5. `visit_record:card:receive` 仅作资格 gate；全局旁观走 `scope_type=global` 规则。

## 非目标

- 管理后台 UI（可先通过 SQL / 脚本 / 内部 API 维护规则）。
- 按商机等更复杂匹配。
- 修改 recorder / leader 版卡片模板内容。
- 替代汇报链上级推送逻辑。

---

## 现有推送分层（不变）

```
send_visit_record_notification
  └─ _collect_visit_record_recipients_and_groups
       ├─ get_recipients_for_recorder          # recorder + leader
       ├─ _get_collaborative_participants_recipients
       ├─ resolve_visit_record_cc_recipients   # notification_cc_rules（user + department + global）
       ├─ department_review 群 / visit_record 简报群
       └─ department_review 命中时：个人列表移除 leader / global 抄送，改推群
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
| `configured_cc` | leader 版 | 配置表解析出的抄送人（`cc_scope=user` / `department` / `global`） |

### 推送优先级（同 open_id 只推一张）

数值越小优先级越高，决定保留哪个 type 的模板：

```
recorder(0) > leader(1) > configured_cc(2) > collaborative_participant(3)
```

---

## 数据模型

### 表：`notification_cc_rules`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT PK | 主键 |
| `event_type` | VARCHAR(64) | 事件类型；v1 固定 `visit_record_card` |
| `scope_type` | VARCHAR(32) | 匹配维度；`user` / `department` / `global` |
| `scope_user_id` | UUID NULL | `scope_type=user` 时：录入人的 `users.id` |
| `scope_department_id` | VARCHAR NULL | `scope_type=department` 时：部门 `department_mirror.unique_id` |
| `include_children` | BOOLEAN DEFAULT FALSE | `scope_type=department` 时：为 true 则录入人所在部门为配置部门的子部门也命中 |
| `priority` | INT DEFAULT 0 | **不用于过滤**；管理端排序、日志、后续扩展 |
| `enabled` | BOOLEAN DEFAULT TRUE | 是否启用 |
| `description` | VARCHAR(512) NULL | 备注 |
| `created_at` / `updated_at` | TIMESTAMP | 审计 |

索引建议：

- `(event_type, scope_type, scope_user_id, enabled)` — 按录入人查规则
- `(event_type, scope_type, scope_department_id, enabled)` — 按部门查规则
- `(event_type, scope_type, enabled)` — 查 global 规则

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

### Step 0：解析录入人 user_id 与部门

从 `recorder_id` / `recorder_name` 查 `user_profiles`，得到 `recorder_user_id`（`user_profiles.user_id`）。  
部门优先取拜访快照 `recorder_department_id`；缺失时查 `user_department_relation` 主部门。  
无法解析 user_id 时：跳过配置抄送（主送仍按现有逻辑尝试）。

### Step 1：查命中规则

```sql
SELECT * FROM notification_cc_rules
WHERE event_type = 'visit_record_card'
  AND enabled = TRUE
  AND (
    (scope_type = 'user' AND scope_user_id = :recorder_user_id)
    OR scope_type = 'global'
    OR (scope_type = 'department' AND scope_department_id = :recorder_department_id)
    OR (
      scope_type = 'department'
      AND include_children = TRUE
      AND scope_department_id IN (:recorder_department_ancestor_ids)  -- 含自身
    )
  )
```

`include_children` 语义与 `department_group_chats` 一致：配置部门在录入人部门的祖先链上即命中（父部门规则覆盖子部门）。

### Step 2：合并规则内抄送人

- 遍历所有命中规则（可按 `priority` DESC 排序，**仅影响遍历顺序，不影响最终名单**）。
- 收集各规则 `notification_cc_rule_recipients.user_id`。
- **按 `user_id` 去重**；同一人同时命中多维度时 `cc_scope` 取更具体者：`user` > `department` > `global`。

### Step 3：解析 profile 并构造 recipient

对配置表抄送人：

1. **批量**查 `user_profiles`（`user_id`），经 `profile.oauth_user`（`oauth_accounts`）取 `open_id`、`platform`；档案不存在、未激活或无 oauth 账号的跳过并打 warn 日志。
2. 暂不强制校验 `visit_record:card:receive` 资格（需要时可在代码内开启）。

### Step 4：构造 recipient

每个通过 Step 3 的 user 追加到 `recipients_by_platform`：

```python
{
    "open_id": ...,
    "name": ...,
    "type": "configured_cc",
    "cc_scope": "user" | "department" | "global",
    "department": ...,
    "receive_id_type": "open_id",
    "platform": ...,
}
```

### Step 5：与主送 / leader 合并后推送

`_send_visit_record_to_individual_recipients` 内：

- 按 `_VISIT_RECORD_RECIPIENT_TYPE_PRIORITY` 排序；
- 以 `(platform, open_id)` 为键去重，每人只发一张卡片。

---

## 与现有逻辑的关系

### 叠加，不替代

| 来源 | 角色 |
|------|------|
| `notification_cc_rules`（`user`） | 按销售 user_id 的差异化抄送 |
| `notification_cc_rules`（`department`） | 按录入人部门的差异化抄送 |
| `notification_cc_rules`（`global`） | 公司级旁观（每次拜访都抄送） |
| 汇报链 `leader` | 管理层，非配置抄送；`max_levels=1` 时 OAuth 无本部门 leader 会 fallback 上级部门主管 |

### department_review 群

部门配置了 `department_review` 群时：

- 个人列表中的 `leader`、`cc_scope=global` / `department` 的 `configured_cc` 被移除；
- leader 版卡片改推 review 群（管理层、global 与 department 级抄送受众一般在群内）；
- 仅 `cc_scope=user` 的 `configured_cc`（按具体销售的个性化抄送）仍推送给个人；
- 个人推送始终保留 `recorder` 与 `collaborative_participant`。

### 汇报链 early-return

`get_recipients_for_recorder` 仅负责 recorder + leader；配置抄送在 `_collect_visit_record_recipients_and_groups` 独立步骤完成，不依赖汇报链结果。

---

## 当前模型

```
路由：notification_cc_rules（scope_type=user | department | global）
资格：visit_record:card:receive / notification:follow_up_card:receive 仅作 gate（代码暂未强制校验）
```

公司旁观名单由 `scope_type=global` 规则维护；OAuth permission 不再作为抄送路由源。

---

## 相关代码

| 模块 | 职责 |
|------|------|
| `app/models/notification_cc_rule.py` | 规则与 recipients 模型 |
| `app/repositories/notification_cc_rule.py` | 按 recorder / 部门查规则并合并 recipients |
| `app/services/visit_record_cc_resolver.py` | 规则 → configured_cc recipients |
| `app/services/platform_notification_service.py` | 收集主送/leader/抄送/群，去重推送 |
| `app/api/routes/...`（可选） | 规则 CRUD 内部 API |

---

## 示例

### 配置

| rule_id | scope_type | scope_user_id | scope_department_id | include_children | recipients |
|---------|------------|---------------|---------------------|------------------|------------|
| 1 | user | 销售-A 的 user_id | NULL | false | [张三, 李四] |
| 2 | user | 销售-A 的 user_id | NULL | false | [李四, 王五] |
| 3 | user | 销售-B 的 user_id | NULL | false | [赵六] |
| 4 | department | NULL | 华东销售 | true | [区域负责人-周] |
| 5 | global | NULL | NULL | false | [高管-陈总] |

### 销售 A（属华东销售子部门）录入拜访

1. 规则 1 + 2 + 4 + 5 命中 → 合并 → [张三, 李四, 王五, 周, 陈总]
2. 若李四同时是汇报链 leader → 最终只推一张 leader 版（leader 优先级高于 configured_cc）
3. 录入人本人 → recorder 版

### 销售 C 录入拜访（无 user / department 规则）

1. 仅 global 规则 5 命中 → [高管-陈总]
2. 与公司旁观预期一致

---

## 测试要点

1. **多规则合并**：同 recorder 两条规则，recipient 并集且 user_id 去重。
2. **global 规则**：任意 recorder 均命中 global recipients。
3. **department 规则**：录入人部门精确匹配；`include_children` 时子部门命中父部门规则。
4. **cc_scope 优先级**：同一人同时命中 user / department / global 时取更具体维度。
5. **open_id 去重**：同一人既是 leader 又是 configured_cc，只推一张，模板取高优先级 type。
6. **汇报链为空**：仍有配置抄送（含 global / department）。
7. **无 profile / 无 oauth 账号**：跳过该抄送人，不影响其他人。
8. **department_review 群**：leader / global / department 抄送走群；仅 `cc_scope=user` 的 configured_cc 仍走个人。

---

## 后续扩展

| 扩展 | 说明 |
|------|------|
| 管理 API / 后台页 | 规则 CRUD、命中预览（输入 recorder → 展示最终抄送名单） |
| 推送审计表 | 记录 rule_id、实际推送 open_id，便于排障 |
| 强制资格 gate | 对 configured_cc 校验 `visit_record:card:receive` |

---

## 决策记录

| 决策 | 结论 | 日期 |
|------|------|------|
| 新规则 vs OAuth 权限 | 路由仅规则表；permission 仅作资格 | 2026-07-15 |
| 多规则命中 | 合并抄送人，不按 priority 取单条 | — |
| 同接收人去重 | user_id（规则层）+ open_id（推送层） | — |
| 匹配维度 | `scope_type=user` + `global` | 2026-07-15 |
| OAuth 全局名单 | 已移除；公司旁观改配 global 规则 | 2026-07-15 |
| 部门维度 | `scope_type=department` + `include_children`；cc_scope 优先级 user > department > global | 2026-07-17 |
| department_review 与部门抄送 | `cc_scope=department` 视作部门级旁观，与 global 一样改由 review 群接收，不再推个人；仅 `user` 保留个人推送 | 2026-07-17 |

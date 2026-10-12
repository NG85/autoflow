# 拜访卡片推送策略（visit_record_push_policy）

SiteSetting 名：`visit_record_push_policy`。  
写入：`PUT /admin/site-settings/visit_record_push_policy`，body 为 `{ "value": <下面 JSON> }`。  
库里没有这条时，用 `default_settings.yml` 默认值（全 `legacy`）。DB 覆盖 YAML。后台写入后约 6 秒生效，不用重启；已发出的卡不会变。

解析代码：`app/services/visit_record_push_policy.py`。无法解析时回退全 `legacy`。

---

## 字段说明

| 角色 | 谁 | lite 时的复盘视角 |
|------|----|-------------------|
| `recorder` | 记录人本人 | sales |
| `collaborative_participant` | 协同人 | sales |
| `leader` | 记录人汇报链上级（max_levels=1） | leader |
| `configured_cc` | `notification_cc_rules` 抄送 | leader |
| `groups.review` | 部门 review 群 | leader |
| `groups.brief` | 部门简报群 | leader |

每个角色：

```yaml
enabled: true          # false 则不推这个角色
card: recap_lite       # recap_lite | legacy
```

简写（写在 `recipients` 或顶层均可）：

- `"recap_lite"` / `"legacy"` → enabled=true，指定卡片
- `false` / `"off"` → 关闭该角色
- 顶层 `strategy: recap_lite` → 未写 `card` 的角色默认 lite（**未写明的角色也会变成 lite**，慎用）

同一个人只发一张卡，优先级：`recorder > leader > configured_cc > collaborative_participant`。

lite 卡拜访区链到详情页 `/v2/behavior/{record_id}`；完整复盘报告链到 `/v2/behavior/{record_id}/recap`；抽取按钮链到 `/v2/behavior/{record_id}/extract?tab={card_links.key}`（如 `follow_ups` / `next_visit` / `key_issues` / `potential_opps` / `conclusion`）。链接里不要带 `view=`，视角由打开页的登录人决定。

详情 GET 不读这项配置，也不返回复盘。复盘用 `GET /crm/visit_records/{record_id}/recap`。没有 `crm_entity_insight` 复盘行时不返回 `insight`，也不查 OAuth 汇报链、不读抽取表。有复盘才按身份选视角：记录人始终 sales（UUID 连字符忽略）；**本条**记录人汇报链上级（含同时是协同人）走 leader；其余协同人 sales；其他人 leader。结构化抽取（含 lite 卡 extract 入口、赞踩/采纳）**仅记录人本人**；协同人/上级/群只看对应视角复盘报告，`extract` 为空。协同人匹配用库内原始 JSON（ask_id/user_id），不用详情里拼好的姓名。汇报链有结果时不再用档案直属上级。

---

## 1. 默认 / 全员旧卡（与历史行为一致）

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "legacy" },
    "leader": { "enabled": true, "card": "legacy" },
    "configured_cc": { "enabled": true, "card": "legacy" },
    "collaborative_participant": { "enabled": true, "card": "legacy" }
  },
  "groups": {
    "review": { "enabled": true, "card": "legacy" },
    "brief": { "enabled": true, "card": "legacy" }
  }
}
```

---

## 2. 销售本人轻量卡，其他人旧卡（当前推荐灰度）

记录人收销售视角 lite；上级 / 抄送 / 协同人 / 群继续原模板。

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "recap_lite" },
    "leader": { "enabled": true, "card": "legacy" },
    "configured_cc": { "enabled": true, "card": "legacy" },
    "collaborative_participant": { "enabled": true, "card": "legacy" }
  },
  "groups": {
    "review": { "enabled": true, "card": "legacy" },
    "brief": { "enabled": true, "card": "legacy" }
  }
}
```

简写等价：

```json
{
  "recorder": "recap_lite",
  "leader": "legacy",
  "configured_cc": "legacy",
  "collaborative_participant": "legacy",
  "groups": {
    "review": "legacy",
    "brief": "legacy"
  }
}
```

记录人若同时是上级，去重后仍发 **recorder lite**。

---

## 3. 销售侧都轻量（记录人 + 协同人），管理层旧卡

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "recap_lite" },
    "collaborative_participant": { "enabled": true, "card": "recap_lite" },
    "leader": { "enabled": true, "card": "legacy" },
    "configured_cc": { "enabled": true, "card": "legacy" }
  },
  "groups": {
    "review": { "enabled": true, "card": "legacy" },
    "brief": { "enabled": true, "card": "legacy" }
  }
}
```

协同人若同时是该拜访汇报上级，去重后发 **leader 旧卡**，不发协同人 lite。

---

## 4. 全员轻量卡

记录人/协同人 sales lite，上级/抄送/群 leader lite。

```json
{
  "strategy": "recap_lite"
}
```

展开后与下面等价：

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "recap_lite" },
    "leader": { "enabled": true, "card": "recap_lite" },
    "configured_cc": { "enabled": true, "card": "recap_lite" },
    "collaborative_participant": { "enabled": true, "card": "recap_lite" }
  },
  "groups": {
    "review": { "enabled": true, "card": "recap_lite" },
    "brief": { "enabled": true, "card": "recap_lite" }
  }
}
```

---

## 5. 个人轻量、群仍旧卡

```json
{
  "strategy": "recap_lite",
  "groups": {
    "review": { "enabled": true, "card": "legacy" },
    "brief": { "enabled": true, "card": "legacy" }
  }
}
```

`strategy` 会作用到未单独写 `card` 的个人角色；群在 `groups` 里显式覆盖为 `legacy`。

---

## 6. 只推记录人轻量卡，关掉上级 / 抄送 / 协同人 / 群

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "recap_lite" },
    "leader": { "enabled": false },
    "configured_cc": { "enabled": false },
    "collaborative_participant": { "enabled": false }
  },
  "groups": {
    "review": { "enabled": false },
    "brief": { "enabled": false }
  }
}
```

简写：`"leader": false`。

---

## 7. 只推个人、不推群（卡片类型按角色混用）

例如销售 lite、上级旧卡、不要群：

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "recap_lite" },
    "leader": { "enabled": true, "card": "legacy" },
    "configured_cc": { "enabled": true, "card": "legacy" },
    "collaborative_participant": { "enabled": true, "card": "legacy" }
  },
  "groups": {
    "review": { "enabled": false },
    "brief": { "enabled": false }
  }
}
```

---

## 8. 管理层 lite，销售仍旧卡

```json
{
  "recipients": {
    "recorder": { "enabled": true, "card": "legacy" },
    "collaborative_participant": { "enabled": true, "card": "legacy" },
    "leader": { "enabled": true, "card": "recap_lite" },
    "configured_cc": { "enabled": true, "card": "recap_lite" }
  },
  "groups": {
    "review": { "enabled": true, "card": "recap_lite" },
    "brief": { "enabled": true, "card": "legacy" }
  }
}
```

---

## 上线注意

1. 先部署含 lite / insight 读取的 Autoflow，再改这项配置。旧进程读到新 key 会当不认识，仍全 legacy。
2. 改 YAML 默认值要重启进程；改 DB SiteSetting 约 6 秒生效。
3. 推卡发生在 Aldebaran 复盘完成回调之后，lite 卡读当时已写入的 `crm_entity_insight`。
4. 复盘报告 path 为 `/v2/behavior/{id}/recap`；抽取入口为 `/extract?tab={key}`，改 path 时改 `push_page_urls`。
5. 列表 data-scope 与推送名单不是同一套，不要靠扩列表来配合这张卡。
6. 记录人 lite 卡在复盘摘要后放「完整复盘报告」，有抽取时飞书用 default 小按钮（`open_url`）挂 `card_links`（待我跟进 / 下次沟通 / 当前最关键问题 / 潜在新商机等，有数据才出现），钉钉仍为 markdown 链接；跳转 `/extract?tab={key}`。没有 `crm_entity_insight` 复盘行时不读抽取表、不加这些入口，与 `GET /recap` 一致。协同人/领导/群 lite 不加这些入口。抽取表缺失或为空时卡片与现在一样，只有 summary + 完整复盘报告。

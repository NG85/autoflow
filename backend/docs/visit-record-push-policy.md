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

`groups.review` 还可以写 `replaces`，表示有拜访群时，哪些个人角色改由群接收、不再单独发：

```yaml
replaces: ["leader", "configured_cc"]   # 缺省。公司级、部门级抄送跟着 configured_cc 走
replaces: []                            # 不接替任何人。群和个人各收各的卡
replaces: ["leader"]                    # 只接替上级，抄送仍发个人
```

不写 `replaces` 等于 `["leader", "configured_cc"]`。`cc_scope=user` 的个性化抄送、记录人、协同人始终单独收，写进名单也不生效。群 `enabled: false` 时不接替，个人照发。简报群不接替个人。

简写（写在 `recipients` 或顶层均可）：

- `"recap_lite"` / `"legacy"` → enabled=true，指定卡片
- `false` / `"off"` → 关闭该角色
- 顶层 `strategy: recap_lite` → 未写 `card` 的角色默认 lite（**未写明的角色也会变成 lite**，慎用）

同一个人只发一张卡，优先级：`recorder > leader > configured_cc > collaborative_participant`。

lite 卡拜访区链到详情页 `/v2/behavior/{record_id}`；完整复盘报告链到 `/v2/behavior/{record_id}/recap`；抽取按钮链到 `/v2/behavior/{record_id}/extract?tab={card_links.key}`（如 `follow_ups` / `next_visit` / `key_issues` / `potential_opps` / `conclusion`）。链接里不要带 `view=`，视角由打开页的登录人决定。

详情 GET 不读这项配置，也不返回复盘。复盘用 `GET /crm/visit_records/{record_id}/recap`。谁能打开、打开后是哪一视角，见 `fsd/2026-11-12-visit-recap-audience.md`。业务推进和人员表现评价都没有时不返回报告，也不查 OAuth 汇报链、不读抽取表。

能打开时 `tabs` 为 `business`（业务推进）和 `performance`（销售表现）。`insight` 是业务推进，`performance` 是人员表现评价，缺的为 `null`。结构化抽取（含 lite 卡 extract 入口、赞踩/采纳）**仅记录人本人**。汇报链有结果时不再用档案直属上级。

轻量卡只把表现评价的 `summary` 放进正文。有摘要时，「业务推进」和「销售表现」各成一行小标题，正文跟在标题下面。飞书用四级标题；钉钉用二级标题，两段之间用不断行空格隔开。记录人、纯协同人用销售视角；leader 个人用上级视角。抄送和部门群不放「销售表现」。空值和 `--` 视为没有评价。旧模板卡不放这段。

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

协同人若同时是该拜访汇报上级，收集时就记成 leader，发 **leader 卡**。有拜访群并且群接替 leader 时，这个人不会再以协同人留下。

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

`strategy` 会作用到未单独写 `card` 的个人角色；群在 `groups` 里显式覆盖为 `legacy`。这里没写 `replaces`，有拜访群时仍接替 leader 和公司级、部门级抄送，leader 的轻量卡不会单独发出。要群旧卡、leader 另收轻量卡，见第 9 节。

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

## 9. 群仍旧卡，leader 另外收轻量卡

只给这家客户写 `replaces: []`。群收旧卡，leader 按自己的 `card` 单独收。其他客户不写这个字段，仍由群接替 leader。

```json
{
  "recipients": {
    "leader": { "enabled": true, "card": "recap_lite" }
  },
  "groups": {
    "review": { "enabled": true, "card": "legacy", "replaces": [] }
  }
}
```

---

## 上线注意

1. 先部署含 lite / insight 读取的 Autoflow，再改这项配置。旧进程读到新 key 会当不认识，仍全 legacy。
2. 改 YAML 默认值要重启进程；改 DB SiteSetting 约 6 秒生效。
3. 推卡发生在 Aldebaran 复盘完成回调之后，lite 卡读当时已写入的 `crm_entity_insight`。
4. 复盘报告 path 为 `/v2/behavior/{id}/recap`；抽取入口为 `/extract?tab={key}`，改 path 时改 `push_page_urls`。lite 卡上「跟进详情 / 完整复盘报告」以及钉钉抽取蓝链，同一行有多条时用「｜」分隔（两边留空格）；飞书抽取仍是独立小按钮。
5. 列表 data-scope 与推送名单不是同一套，不要靠扩列表来配合这张卡。
6. 记录人 lite 卡在摘要后放「完整复盘报告」，有业务推进且有抽取时飞书用 default 小按钮（`open_url`）挂 `card_links`（待我跟进 / 下次沟通 / 当前最关键问题 / 潜在新商机等，有数据才出现），钉钉写成同行 markdown 蓝链并用「｜」分隔；跳转 `/extract?tab={key}`。没有业务推进时卡片不读抽取表、不加这些入口。只有表现评价时，复盘页对记录人仍返回抽取。协同人/领导/群 lite 不加这些入口。抽取表缺失或为空时卡片只有摘要和完整复盘报告。有表现评价摘要时，「销售表现」是一行小标题，摘要跟在下面。

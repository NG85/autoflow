# 功能规格说明书：销售今日重点读取关键发现类型

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-16 |
| 变更窗口 | 2026-11-16 |
| 前置说明书 | `fsd/2026-11-10-sales-highlights-key-findings.md` |
| 关联服务 | backend；Aldebaran；飞书 / Lark / 钉钉 / 企微 |
| 关联功能模块 | crm/today_highlights、notification |
| 前端接入 | `backend/docs/workbench-today-highlights-frontend.md`、`backend/docs/today-highlights-list-query.md` |

本文件给关键发现补上抽取类型。展示名和页面 tab 沿用 2026-11-10 的说明书。历史说明书不改。团队今日重点不在这次范围。

## 1. 这次做什么

Aldebaran 新写入的关键发现，抽取类型是 `KEY_FINDING`。卡片蓝链、页面 tab、已发出卡片上的链接，仍归在 `open_risks`。地址继续是 `/v2/today-highlights?date=YYYY-MM-DD&tab=open_risks`。

旧抽取 `OPEN_RISK` 进同一块，不回写。快照 key 为 `key_findings` 的新行也并进这一块。

## 2. 谁写入

正文由 Aldebaran 写入。本服务只读，并继续写赞踩与采纳。

新的抽取行：

| 列 | 值 |
| --- | --- |
| `item_type` | `KEY_FINDING` |
| `subject_type` | `USER` |
| `audience` | `sales` |
| `card_links[].key` | `key_findings` |
| `card_links[].title` | 关键发现 |

历史行仍是 `item_type=OPEN_RISK`，快照 key 为 `open_risks`，标题可能是「商机风险」或「建议关注」。

工作台列表读 `crm_entity_insight`，不读抽取表。这一类新洞察的 `insight_type` 是 `KEY_FINDING`，历史行 `DAILY_SALES_RISK` 仍算关键发现。点进去的 tab 仍是 `open_risks`。

## 3. 销售看到什么

`KEY_FINDING` 和 `OPEN_RISK` 都出现在「关键发现」。每条保留自己的 `item_type`。

标题优先用 `key_findings` 快照。没有这条快照时，用 `open_risks` 快照。「商机风险」「建议关注」展示成「关键发现」。已经是「关键发现」或其它文案的不改。

`gap_only`、`not_formal_risk`、`risk_change=unchanged` 不展示。`risk_change=resolved` 主张前加「已闭环」，并排在该区块最后。其余按置信度从高到低。

采纳、拒绝、赞踩与原先的关键发现相同。立即处理仍不采纳、不拒绝。

## 4. 验收

- 同一天既有 `KEY_FINDING` 又有 `OPEN_RISK` 时，销售卡蓝链和销售页只有一个「关键发现」，地址是 `tab=open_risks`。
- 快照 key 是 `key_findings` 时，蓝链和页面地址仍是 `tab=open_risks`。已发出的 `tab=open_risks` 链接仍打开这一块。
- 快照标题是「商机风险」或「建议关注」时，展示成「关键发现」。同时有 `key_findings` 快照时，用这份快照的标题。
- `unchanged`、`gap_only`、`not_formal_risk` 的 `KEY_FINDING` 不进卡，也不挡住同一天的其它关键发现。
- 团队今日重点的文案和 tab 不变。

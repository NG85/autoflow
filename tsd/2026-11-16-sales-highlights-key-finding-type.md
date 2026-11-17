# 技术规格说明书：销售今日重点读取关键发现类型

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-16 |
| 变更窗口 | 2026-11-16 |
| 前置说明书 | `tsd/2026-11-10-sales-highlights-key-findings.md` |
| 关联服务 | backend；Aldebaran |
| 关联功能模块 | crm/today_highlights、notification |

用户可见行为见 `fsd/2026-11-16-sales-highlights-key-finding-type.md`。没有新增表或迁移。`crm_skill_extract_items` 与 `crm_entity_insight` 仍由 Aldebaran 写入。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 类型归并、快照 key 归并、标题替换 | `backend/app/services/sales_today_highlights.py` |
| 采纳与拒绝 | `backend/app/services/sales_immediate_actions.py`。非立即处理的本人抽取可写，`KEY_FINDING` 走同一路径 |
| 列表口径 | `backend/docs/today-highlights-list-query.md`。本服务的销售卡不查 `crm_entity_insight` |
| 页面字段 | `backend/docs/workbench-today-highlights-frontend.md` |
| 单测 | `backend/tests/test_sales_today_highlights.py`、`backend/tests/test_sales_immediate_actions.py` |

## 2. 读取

`open_risks` 接受的 `item_type` 按这个顺序：`KEY_FINDING`、`OPEN_RISK`。两条都要过原来的过滤：`gap_only`、`not_formal_risk` 为真，或 `risk_change=unchanged`，整行不进。同一 `item_type`、同一 `extract_key` 只留最新一行。两种类型不互相覆盖。

对外区块 key 固定为 `open_risks`。`build_sales_today_highlights_url` 用这个 key，链接参数是 `tab=open_risks`。

抽取快照上的 card key 先映到区块：`key_findings` 和 `open_risks` 都是 `open_risks`。同一区块有 `key_findings` 快照时用它的 title，否则用 `open_risks` 快照。之后才做标题替换：「商机风险」「建议关注」换成「关键发现」。不回写抽取行。

`highlights_payload` 的 `items` 和 `card_links[].key` 都是 `open_risks`。`card_links[].item_types` 只列出这次实际出现的类型，`KEY_FINDING` 在 `OPEN_RISK` 前面。每条 `items[].item_type` 保持库里的值。

## 3. 上游写入

Aldebaran 新行：

```text
crm_skill_extract_items.item_type     = KEY_FINDING
crm_skill_extract_items.subject_type  = USER
crm_skill_extract_items.audience      = sales
card_links[].key                      = key_findings
card_links[].title                    = 关键发现
crm_entity_insight.insight_type       = KEY_FINDING
```

历史抽取 `OPEN_RISK`、历史洞察 `DAILY_SALES_RISK`、历史快照 key `open_risks` 继续有效。本服务不改这些旧行。

# 功能规格说明书：负责人今日重点报告入口

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-27 |
| 变更窗口 | 2026-10-27 |
| 前置说明书 | `fsd/2026-10-23-comment-edit.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |
| 前端接入 | `backend/docs/workbench-today-highlights-frontend.md` |

本文件描述负责人今日重点相对 `fsd/2026-10-19-release.md` 新增的三个报告入口，以及发卡和列表的相应调整。历史说明书不改。一线销售今日重点不在这次范围内。

## 1. 这次做什么

负责人工作台「团队」这一块，除今日需介入外，再展示三个只读报告：重点商机、团队人效、经营趋势。飞书卡片上是入口蓝链，页面上是完整报告正文。数据按负责人本人取，不按部门分段。

## 2. 谁能看到

和需介入同一套人：当前登录人的 oauth id。没有 oauth 身份时列表为空。不接受别人的用户 id，也不接受 `department_id`。

写入仍只针对今日需介入。报告没有采纳、拒绝、评论、挂任务、赞踩。用报告的 `unique_id` 去写会 404。

## 3. 页面

`GET /crm/today-highlights/departments?report_date=YYYY-MM-DD`。`report_date` 缺省为北京时间当天。

返回按人一份：`report_date`、`subject_id`（oauth id，不要展示）、`items`、`card_links`。不要按部门分段，响应里没有 `departments`、`department_id`、`department_name`。

tab 只来自这次返回的 `card_links`，按返回顺序排，文案用 `card_links[].title`。当天没有的报告不要占空 tab。`tab` 对不上或没传时打开第一个。

| tab | 标题 | 内容 |
| --- | --- | --- |
| `leader_intervene` | 今日需介入 | 与原来相同，可写入 |
| `key_deal_review` | 重点商机 | 只读报告 |
| `leader_efficiency_insight` | 团队人效 | 只读报告 |
| `team_operating_trend` | 经营趋势 | 只读报告 |

报告每个区块至多一条。条目只含页面正文：

| 字段 | 含义 |
| --- | --- |
| `unique_id` | 列表用，只读 |
| `summary` | 摘要，优先展示 |
| `detail_text` | 完整正文，可折叠 |

没有 `title`、`item_type`、`evidence`、`severity`、`category`。写入方标题可能带姓名、日期，不要拿来当 tab 名或主标题。摘要和全文都空时，这个区块不出现。当天既没有需介入也没有报告时，`items` 为 `{}`，`card_links` 为 `[]`。

从卡片打开：

```text
/v2/department-highlights?date=YYYY-MM-DD&tab={card_links.key}
```

链接不带 `department_id`。

## 4. 卡片

负责人一人一天一张卡，只发个人，不进部门群，不抄送。标题仍是「今日重点｜日期」，不写部门名。

顺序固定：

1. 今日需介入：只展开置信度最高的一条，其余人写成「还有姓名」。只有一条时写「查看」，还有别的时写「更多（N）」。
2. 周一、周五的团队拜访对照。规则与 `fsd/2026-10-19-release.md` 3.2.3 相同。页面列表没有这段。
3. 报告入口：一行蓝链，文案为「重点商机」「团队人效」「经营趋势」。有匹配到的洞察才出现，没有的不占位。都打开今日重点页对应 tab。

飞书把后两段放在分隔线后面。钉钉用空行隔开。

只有报告、没有需介入也没有拜访对照时仍发卡片，正文先写「今天没有需介入事项，可以先看这些报告。」再跟蓝链。有需介入或拜访对照时不写这句。

需介入、拜访对照、报告都没有才跳过，不发空卡。

团队人效、经营趋势由写入方在周一、周五当天写出、当天进卡。卡片文案不带上周/本周。重点商机按当天有没有匹配洞察决定。

底部「本周已拜访」规则不变：周一不放；周一、周五有拜访对照时入口改在那段末尾，底部不再重复；周二到周日没有对照时仍可放。

## 5. 数据口径

需介入：`crm_skill_extract_items`，`subject_type=USER`，`audience=leader`，`item_type=TODAY_INTERVENTION`，`subject_id` 是负责人 oauth id。

报告：`crm_entity_insight`，`entity_type=USER`，`entity_id` 是同一套 oauth id，`period_key` 等于当天 `report_date`（YYYY-MM-DD，写入方存执行日）。

| 入口 | `insight_type` |
| --- | --- |
| 重点商机 | `KEY_DEAL_REVIEW` |
| 团队人效 | `LEADER_EFFICIENCY_INSIGHT` |
| 经营趋势 | `TEAM_OPERATING_TREND` |

每个槽位取未删除、未过期的最新一条。查不到或筛不到就不出现，不挡住需介入推送。

## 6. 验收

- 卡片和页面的三个入口文案是重点商机、团队人效、经营趋势。没有的不占位。
- 点入口打开 `/v2/department-highlights?date=&tab=`，不跳到周报旧地址，不带 `department_id`。
- 列表按当前登录人一份返回。报告条目只有 `unique_id`、`summary`、`detail_text`。
- 报告 tab 不能采纳、评论、赞踩。
- 只有报告也会发卡片，并出现「今天没有需介入事项，可以先看这些报告。」
- 需介入、拜访对照、报告都没有时不发空卡。
- 变体关闭、没有 open_id、负责人关掉今日重点时 skip。
- 一线销售今日重点不受影响。

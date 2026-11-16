# 今日重点列表：查询口径

给协作方做「今日重点」汇总列表用。列表按类型各占一行，只查 `crm_entity_insight`。点进去打开现有详情页，不要另做正文页。

详情页和写入规则见 `backend/docs/workbench-today-highlights-frontend.md`。

抽取项来自这些洞察的 markdown。推送和现有详情页里，个人三类、团队需介入仍按抽取出的条目展示。列表不查 `crm_skill_extract_items`。

| 列表分组 | 谁能看到 | 人怎么对上 | 详情页 |
| --- | --- | --- | --- |
| 个人（我的） | 当前登录人 | `entity_type=USER`，`entity_id` = 当前登录人 oauth id | `/v2/today-highlights?date=&tab=` |
| 团队 | 当前登录人 | 同上，同一 oauth id | `/v2/department-highlights?date=&tab=` |
| CXO | 仅 `biz_admin`、`sys_admin`、`executive` | 先过角色。通过后 `entity_type=DEPARTMENT`，不按登录人 id 过滤 | `/v2/company-highlights?date=&tab=` |

个人和团队不做角色判断，只按当前登录人筛。同一个人可以两块都有。请求里不要传别人的用户 id。

CXO 先看当前登录人的角色，是 `biz_admin`、`sys_admin`、`executive` 之一才查。这三类之外的人，包括公司今日重点的推送接收人，列表里不要出现 CXO。角色查询失败时也不要露出。

日期用 `period_key`，值是业务日 `YYYY-MM-DD`。链接上的参数名是 `date`，详情接口改名为 `report_date`。

个人页、团队页打开的是登录人自己的内容，地址里不要带别人的用户 id，也不要带 `department_id`。CXO 页打开的是当天全部范围。

星期不要写进查询。团队人效、经营趋势、CXO 重点商机盘点、下周待确认通常只在对应业务日有行。当天没有这一行，这一类就不出现。

拜访对照是团队卡上当天现算的一段，没有洞察行，也没有 tab，不要放进列表。

公共条件：

```text
period_key    = 业务日
is_deleted    = 0
expires_at IS NULL OR expires_at > UTC_TIMESTAMP()
```

每种、每个 `entity_id` 取 `generated_at`、`id` 最新的一条。`summary` 和 `detail_text` 都空则这一类不出现。

洞察在结论为「推送 0 项」时仍会落一行，摘要里写明没有可推送的条目。列表按这一行出现。详情页只渲染抽取出的条目，这种日子点进去对应 tab 可以是空的。

---

## 个人

`entity_type=USER`，`entity_id` = 当前登录人 oauth id。一个人一种一天一行。

| 列表文案 | `insight_type` | 详情里的抽取 | `tab` |
| --- | --- | --- | --- |
| 立即处理 | `DAILY_IMMEDIATE_ACTION` | `IMMEDIATE_ACTION` | `immediate_today` |
| 关键发现 | `KEY_FINDING`（历史行 `DAILY_SALES_RISK` 仍读） | `KEY_FINDING`（旧行 `OPEN_RISK` 仍读；快照 key `key_findings` 与 `open_risks` 同一块） | `open_risks` |
| 下周待确认 | `NEXT_WEEK_ACTION_CONFIRM` | `NEXT_WEEK_ACTION_CANDIDATE` | `next_week_confirm` |

跳转：

```text
/v2/today-highlights?date=YYYY-MM-DD&tab=immediate_today
/v2/today-highlights?date=YYYY-MM-DD&tab=open_risks
/v2/today-highlights?date=YYYY-MM-DD&tab=next_week_confirm
```

详情数据是 `GET /api/v1/crm/today-highlights/sales?report_date=`。这个接口只返回当前登录人，读的是抽取表。

---

## 团队

`entity_type=USER`，`entity_id` = 当前登录人 oauth id。一个人、一天最多四行。`entity_type=DEPARTMENT` 的行属于 CXO，不要放进团队。

| 列表文案 | `insight_type` | 详情里的抽取 | `tab` |
| --- | --- | --- | --- |
| 今日需介入 | `DAILY_LEADER_INTERVENE` | `TODAY_INTERVENTION` | `leader_intervene` |
| 重点商机 | `KEY_DEAL_REVIEW` | 无，详情直接展示这份洞察 | `key_deal_review` |
| 团队人效 | `LEADER_EFFICIENCY_INSIGHT` | 无，详情直接展示这份洞察 | `leader_efficiency_insight` |
| 经营趋势 | `TEAM_OPERATING_TREND` | 无，详情直接展示这份洞察 | `team_operating_trend` |

跳转不带 `department_id`：

```text
/v2/department-highlights?date=YYYY-MM-DD&tab=leader_intervene
/v2/department-highlights?date=YYYY-MM-DD&tab=key_deal_review
/v2/department-highlights?date=YYYY-MM-DD&tab=leader_efficiency_insight
/v2/department-highlights?date=YYYY-MM-DD&tab=team_operating_trend
```

详情数据是 `GET /api/v1/crm/today-highlights/departments?report_date=`。需介入读抽取表，三份报告读洞察表。这个接口只返回当前登录人。

---

## CXO

角色通过之后查 `entity_type=DEPARTMENT`，不按登录人的 oauth id 过滤。

| 列表文案 | `insight_type` | `tab` |
| --- | --- | --- |
| 公司人效 | `CXO_EFFICIENCY_INSIGHT` | `cxo_efficiency_insight` |
| 重点商机盘点 | `CXO_KEY_DEAL_REVIEW` | `cxo_key_deal_review` |

`entity_id` 是部门 id。公司级是 `__COMPANY__`。同一种、同一个 `entity_id` 取最新一条。列表上一种一天一行，多个部门合成这一行，不要按部门拆开。公司级在详情页排在部门前面。

`entity_type=USER` 的 `KEY_DEAL_REVIEW` 是团队重点商机，和这里的 `CXO_KEY_DEAL_REVIEW` 不是同一类。

跳转：

```text
/v2/company-highlights?date=YYYY-MM-DD&tab=cxo_efficiency_insight
/v2/company-highlights?date=YYYY-MM-DD&tab=cxo_key_deal_review
```

详情数据是 `GET /api/v1/crm/today-highlights/company?report_date=`。详情接口对推送接收人仍可能返回正文；这个列表不跟着放行。

---

## 类型对照

| | 个人 | 团队需介入 | 团队报告 | CXO |
| --- | --- | --- | --- | --- |
| 表 | `crm_entity_insight` | `crm_entity_insight` | `crm_entity_insight` | `crm_entity_insight` |
| 谁 | 当前登录人 | 当前登录人 | 当前登录人 | 仅 `biz_admin`、`sys_admin`、`executive` |
| 范围 | `entity_type=USER`，`entity_id` = 当前登录人 | 同左 | 同左 | `entity_type=DEPARTMENT` |
| `insight_type` | `DAILY_IMMEDIATE_ACTION`、`KEY_FINDING`（历史 `DAILY_SALES_RISK`）、`NEXT_WEEK_ACTION_CONFIRM` | `DAILY_LEADER_INTERVENE` | `KEY_DEAL_REVIEW`、`LEADER_EFFICIENCY_INSIGHT`、`TEAM_OPERATING_TREND` | `CXO_EFFICIENCY_INSIGHT`、`CXO_KEY_DEAL_REVIEW` |
| 日期 | `period_key` | `period_key` | `period_key` | `period_key` |

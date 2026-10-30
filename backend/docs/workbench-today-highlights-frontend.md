# 今日重点：前端接入

工作台顶上两块，名字用 **我的** 和 **团队**。同一个人可以两块都有，点哪张卡片就打开对应那一块，不要按 `SALES` / `SALES_MANAGER` 只留一块。某一块没有数据时不展示。

| 工作台 | 谁的内容 | 页面 | 列表接口 | 页面上做什么 |
| --- | --- | --- | --- | --- |
| 我的 | 当前用户自己的抽取 | `/v2/today-highlights` | `GET /api/v1/crm/today-highlights/sales` | 建议关注、下周待确认可以采纳/拒绝和赞踩。立即处理已有关联待办时只给一个查看任务的入口：一条打开详情，多条打开任务列表。不展示「已采纳」。采纳和拒绝只出现在还是待判断、且没有关联待办的立即处理上。 |
| 团队 | 当前用户作为负责人的需介入和报告 | `/v2/department-highlights` | `GET /api/v1/crm/today-highlights/departments` | 只对「今日需介入」做采纳/拒绝、评论、挂任务、赞踩。重点商机、团队人效、经营趋势只读，不要画出这些操作。 |

登录态沿用现有 CRM 接口。两个列表都只认当前登录人，请求里不要传别人的用户 id。

销售今日重点用自己的两个 `POST`，不要调负责人的四个写入接口。三个区块用同一套写入。

成功响应一律是：

```json
{ "code": 0, "message": "success", "data": {} }
```

失败是 HTTP 错误，body 为 `{ "detail": "..." }`。下面各写入接口标了状态码。

---

# 一线销售

## 1. 从卡片进页面

飞书卡片链到：

```text
/v2/today-highlights?date=YYYY-MM-DD&tab=immediate_today
/v2/today-highlights?date=YYYY-MM-DD&tab=open_risks
/v2/today-highlights?date=YYYY-MM-DD&tab=next_week_confirm
```

| URL 参数 | 含义 |
| --- | --- |
| `date` | 业务日。调接口时改名为 `report_date`。缺省按北京时间当天。 |
| `tab` | 要打开的 tab，取值是 `card_links[].key`。和拜访复盘抽取页的 `tab` 相同。 |

卡片正文只放最紧急的一条「立即处理」。标题始终是小节名，不跳转。正文下面固定一个入口：只有这一条时写「查看」，还有别的时写「更多（N）」，N 是卡片上没展开的条数，都链到 `tab=immediate_today`。建议关注、下周待确认是卡片上的蓝链，多条时文案带条数，例如「建议关注（3）」，分别链到对应 `tab`。

卡片底部还有两个离开本页的按钮，页面里不用再做：

| 按钮 | 何时出现 | 打开 |
| --- | --- | --- |
| 本周待完成 | 能解析到本人姓名时 | 现有任务列表：本人、预计完成日在本周一到本周日、`status__in=IN_PROGRESS` 与 `status__in=PENDING`（含已逾期） |
| 本周已拜访 | 周二到周日，且能解析到本人姓名 | 现有拜访列表：本人、拜访日期在本周一到本周日 |

周一没有「本周已拜访」。

## 2. 页面长什么样

和拜访复盘抽取页一样，一类一个 tab。tab 不写死，按返回的 `card_links` 顺序渲染：文案用 `title`，旁边带 `count`。`items` 里没有、`card_links` 里也没有的类不出现，不要留空 tab。

当前会返回的类：

1. **立即处理** `immediate_today`
2. **建议关注** `open_risks`。没变化的风险服务端已经去掉。
3. **下周待确认** `next_week_confirm`。只在周五出现。

一次请求带回全部 `items`。页面只展示当前 tab 对应的 `items[key]`。`tab` 能对上某个 `card_links.key` 时打开那个 tab；对不上或没传时打开第一个 tab。切换 tab 时把 `tab` 写回地址栏，日期保持不变。

建议关注、下周待确认可以采纳或拒绝，也可以赞或踩。立即处理只有 `status` 为 `PENDING` 且 `related_todos` 为空时，才显示「采纳」「拒绝」。已经有关联待办时，不展示「已采纳」或其它状态，也不要采纳或拒绝；一条立即处理只放一个「查看任务」。赞踩和这两类操作互不影响。销售页没有评论和建任务。

还在待判断、没有关联待办：

```text
确认排期                                          待判断
客户承诺
甲客户 · 乙商机
今天不定就延期

为什么：……
依据                                              赞  踩

[ 采纳 ]  [ 拒绝 ]
```

已经有关联待办时，一条立即处理只出现一次，标题旁不写状态。来源用下面的中文，放在主张下面。再放一个「查看任务」：

```text
收口两条逾期待办
拜访约定
甲客户
今天不定就延期

为什么：……
依据                                              赞  踩

[ 查看任务 ]
```

`source` 空着就不显示。有值时用这些文案，不要把英文枚举直接露出来：

| `source` | 页面文案 |
| --- | --- |
| `customer_commitment` | 客户承诺 |
| `sales_commitment` | 自己承诺 |
| `leader_requirement` | 领导要求 |
| `risk_confirmed_action` | 风险已确认要做 |
| `visit_confirmed_action` | 拜访约定 |
| `crm_next_step` | 系统里的下一步 |

查看任务用前端现有的任务页，不要另做地址：

- `related_todos` 只有一条：打开详情 `/v2/task/{unique_id}`。`unique_id` 就是这条待办的 `crm_todos.unique_id`，和任务列表点进详情用的是同一个路径。
- 多于一条：打开列表 `/v2/task?unique_id__in={编号}&unique_id__in={编号}`。每个编号一个 `unique_id__in`，和列表页其它 `__in` 条件一样。列表只显示这些待办，并直接打开表格。

立即处理多写「为什么是今天」「做完的标准」「拖下去的影响」。建议关注在 `risk_change` 为 `resolved` 时，主张前加「已闭环」，并排在该区块最后，其余按置信度从高到低。下周待确认的主文案用 `confirm_prompt`，没有再用 `claim`，可带覆盖范围 `coverage` 和时间 `next_week_when`。

立即处理：只有待判断且没有关联待办时，点「采纳」立刻提交，不要带 `adopted_todo_id`；点「拒绝」先收原因，可以空着提交。`related_todos` 不为空时不要提交采纳或拒绝。建议关注、下周待确认仍是已经采纳的可以再拒绝，已经拒绝的可以再采纳，后一次覆盖前一次。没有回到「待判断」的操作。赞和踩再点一次当前图标就是撤销，点另一个就是切换。可以给踩写一句附言，最多 500 字。

整页 `items` 为空就是空状态。

## 3. 拉列表

```http
GET /api/v1/crm/today-highlights/sales?report_date=2026-10-13
```

`report_date` 可不传。没有 `department_id`。当前用户没有 oauth 身份，或当天没有条目时，HTTP 200，`items` 为 `{}`。

```json
{
  "report_date": "2026-10-13",
  "subject_id": "oauth-user-id",
  "items": {
    "immediate_today": []
  },
  "card_links": [
    {
      "key": "immediate_today",
      "title": "立即处理",
      "count": 1,
      "url": "https://.../v2/today-highlights?date=2026-10-13&tab=immediate_today",
      "item_types": ["IMMEDIATE_ACTION"]
    }
  ]
}
```

区块标题用 `card_links[].title`，没有再退回 `key`。`subject_id` 是当前用户的 oauth id，不要展示。缺席的区块不会出现在 `items` 里。

条目字段：

| 字段 | 用在哪 |
| --- | --- |
| `unique_id` | 后续写入都用它。重新进页要再拉列表。不要拿去调负责人接口。 |
| `item_type` | `IMMEDIATE_ACTION`、`OPEN_RISK`、`NEXT_WEEK_ACTION_CANDIDATE`。 |
| `claim` | 主文案。下周区块在没有 `confirm_prompt` 时用它。 |
| `why` | 原因，可折叠。 |
| `confidence` | 数字或空。服务端已排好序。 |
| `evidence` | 依据，数组。没有时是 `[]`。 |
| `account_id` / `account_name` | 客户。名字查不到时为 `null`。 |
| `opportunity_id` / `opportunity_name` | 商机。同上。 |
| `why_today` | 立即处理：为什么是今天。 |
| `delay_impact` | 立即处理：拖下去的影响。 |
| `done_when` | 立即处理：做完的标准。 |
| `source` | 立即处理的来源。空着不展示。有值时用中文：客户承诺、自己承诺、领导要求、风险已确认要做、拜访约定、系统里的下一步。 |
| `risk_status` | 风险状态，小写，可空。 |
| `risk_change` | 风险变化，小写。`resolved` 表示已闭环。 |
| `confirm_prompt` | 下周待确认的主文案。 |
| `coverage` | 下周覆盖范围，小写，可空。 |
| `next_week_when` | 下周时间，可空。 |

三个区块都带下面这些字段。

| 字段 | 含义 |
| --- | --- |
| `status` | `PENDING` 待判断，`ADOPTED` 已采纳，`REJECTED` 已拒绝。立即处理只要 `related_todos` 不为空，页面不展示这个状态。 |
| `reject_reason` | 拒绝原因。未拒绝时 `null`。 |
| `adopted_todo_id` | 返回的这一条上写下的任务编号。同一条立即处理有多条任务时，这是其中第一条。没有或已拒绝时为 `null`。完整列表看 `related_todos`。 |
| `related_todos` | 这条立即处理挂上的任务，`[{ unique_id, title }]`。没有时是 `[]`。不为空时页面以立即处理为一条入口，只放一个「查看任务」：一条打开 `/v2/task/{unique_id}`，多条打开 `/v2/task?unique_id__in={编号}&unique_id__in={编号}`。不要按任务再拆成多条立即处理，也不要展示「已采纳」。 |
| `feedback` | `up`、`down` 或 `null`。 |
| `feedback_comment` | 赞踩附言。撤销后为 `null`。 |
| `feedback_at` | 北京时间，无时区。未赞踩时 `null`。 |

同一条抽取当天被重新抽取后，列表仍只有一条。正文用新的说法，`unique_id`、采纳状态和赞踩留在已经采纳或拒绝、不会被作废的那一行上。

## 4. 采纳 / 拒绝

```http
POST /api/v1/crm/today-highlights/sales/items/decisions
```

请求体、成功时的 `results`、以及「带了任务编号就要先核对、核对失败整批 422」这些规则，与负责人页的采纳接口相同。差别只有：

- 只能写当前用户自己的销售抽取，三个区块都可以。别人的条目、负责人的需介入，在 `results` 里 `ok` 为 false，不会改状态。
- 不写评论，也不在这里建任务或挂任务。采纳可以不带 `adopted_todo_id`；不带时保留这一行已经写下的任务编号，不会写成空。带了新的编号就换成这一条。
- 立即处理只有 `status` 为 `PENDING` 且 `related_todos` 为空时，页面才提交采纳或拒绝。已经有关联待办的不要调这个接口，页面只放一个查看任务的入口：一条去详情，多条去带 `unique_id__in` 的任务列表。

## 5. 赞 / 踩

```http
POST /api/v1/crm/today-highlights/sales/items/{unique_id}/feedback
```

请求体和成功响应与负责人页的赞踩相同。撤销传 `{ "feedback": null }`。找不到或不是自己的抽取时 **404**，`detail` 为「今日重点条目不存在或无权限」。赞踩不改 `status`。

---

# 部门负责人

登录态沿用现有 CRM 接口。列表按当前登录人一份返回，不要传别人的用户 id，也不要按部门分段。

## 6. 从卡片进页面

飞书卡片链到：

```text
/v2/department-highlights?date=YYYY-MM-DD&tab=leader_intervene
/v2/department-highlights?date=YYYY-MM-DD&tab=key_deal_review
/v2/department-highlights?date=YYYY-MM-DD&tab=leader_efficiency_insight
/v2/department-highlights?date=YYYY-MM-DD&tab=team_operating_trend
```

| URL 参数 | 含义 |
| --- | --- |
| `date` | 业务日。调接口时改名为 `report_date`。缺省按北京时间当天。 |
| `tab` | 要打开的 tab，取值是这次列表里的 `card_links[].key`。不要写死四个 tab。和拜访复盘、销售今日重点的 `tab` 相同。 |

标题始终是小节名，不跳转。需介入正文下面固定一个入口：只有这一条时写「查看」，还有别的时写「更多（N）」，N 是卡片上没展开的条数。报告入口是一行蓝链，文案是「重点商机」「团队人效」「经营趋势」，没有的不出现。

页面 tab 只来自 `card_links`，按返回顺序排，文案用 `card_links[].title`。当天没有的报告不要占空 tab。`tab` 对不上或没传时打开第一个。切换 tab 时把 `tab` 写回地址栏。

周一、周五的推送正文在需介入后面还会多一段拜访对照，页面列表里没有这段。周一对照上一周，周五对照本周。需介入只展开一条。其余人在「更多」前面写成「还有姓名」，不展开事项。周一、周五这段先写「{部门名}总拜访 N 条」，口径和一线「本周已拜访」相同：按录入人、拜访沟通日，同一拜访只计最新一条销售视角复盘。卡片上不写具体日期。负责人本人计入总数，不参与点名。然后再点名两类成员：拜访偏少（团队有人录入时，这一周 0 条的人；全员都是 0 不点名），以及需关注偏多（severity=HIGH 至少 2 条，且不少于人均的两倍）。没有要点名的人时，总数仍然保留。这段末尾是「查看上周拜访」或「查看本周拜访」，打开同一周的拜访列表，并带上卡片上的部门名。有这段时，卡片底部不再另放「本周已拜访」。没有在职成员时不加这一段，底部仍按周二到周日放「本周已拜访」，并带上卡片上的部门名。周一、周五没有需介入、但有这段拜访对照时，仍然发卡片。只有报告入口时也发，正文先写「今天没有需介入事项，可以先看这些报告。」再跟蓝链。有需介入或拜访对照时不写这句。

拜访对照后面再跟三个报告入口，有匹配到的洞察才出现，没有的不占位。都打开今日重点页对应 tab，不跳到周报旧地址。团队人效、经营趋势只在周一、周五展示，也是当天任务写出、当天用进卡片。卡片文案不带上周/本周。顺序固定：

| 入口 | `tab` | 打开 |
| --- | --- | --- |
| 重点商机 | `key_deal_review` | `/v2/department-highlights?date=YYYY-MM-DD&tab=key_deal_review` |
| 团队人效 | `leader_efficiency_insight` | `/v2/department-highlights?date=YYYY-MM-DD&tab=leader_efficiency_insight` |
| 经营趋势 | `team_operating_trend` | `/v2/department-highlights?date=YYYY-MM-DD&tab=team_operating_trend` |

报告按负责人本人取，和需介入抽取同一套人：`crm_entity_insight.entity_type=USER`，`entity_id` 是 oauth id。链接不带 `department_id`。`period_key` 等于当天 `report_date`（写入方当天执行，存的是执行日）。需介入对应 `DAILY_LEADER_INTERVENE`；重点商机是 `KEY_DEAL_REVIEW`；团队人效是 `LEADER_EFFICIENCY_INSIGHT`；经营趋势是 `TEAM_OPERATING_TREND`。这三项有匹配时就会发卡片，不必再等需介入或拜访对照。需介入、拜访对照、报告都没有才跳过。

---

## 7. 页面上三块操作（仅今日需介入）

这三块只出现在 `leader_intervene`。重点商机、团队人效、经营趋势不要画这些按钮，也不要调这四个地址。销售今日重点同样不要调用。

一张卡上三块互不影响，写的也是不同接口。

| 区块 | 用户在做什么 | 接口 |
| --- | --- | --- |
| 采纳 / 拒绝 | 认不认同这条建议 | `POST .../items/decisions` |
| 评论、建任务 | 实际做了什么处理 | `POST .../items/{unique_id}/comments`、`POST .../items/{unique_id}/tasks` |
| 赞 / 踩 | 这条系统判断准不准 | `POST .../items/{unique_id}/feedback` |

采纳不会自动出现一条评论或一条任务。赞踩也不会改采纳状态。拒绝会清掉「这条决定关联的任务编号」，已经挂在卡片上的任务列表还在。

没有「改回待判断」。只有采纳和拒绝，后一次覆盖前一次。

---

## 8. 拉列表

```http
GET /api/v1/crm/today-highlights/departments?report_date=2026-10-13
```

`report_date` 可不传。没有 oauth 身份，或当天没有需介入也没有报告时，HTTP 200，`items` 为 `{}`，`card_links` 为 `[]`。

只返回当天有内容的区块。不要假定一定有 `key_deal_review` / `leader_efficiency_insight` / `team_operating_trend`。没有的 key 不会出现在 `items` 和 `card_links` 里，不要补空数组、不要画空 tab。

```json
{
  "report_date": "2026-10-13",
  "subject_id": "oauth-leader",
  "items": {
    "leader_intervene": [
      {
        "unique_id": "item-1",
        "claim": "特价必须今天拍板",
        "leader_action": "今天确认",
        "related_user_name": "张三",
        "status": "PENDING"
      }
    ],
    "key_deal_review": [
      {
        "unique_id": "insight-1",
        "summary": "一条摘要",
        "detail_text": "完整正文"
      }
    ]
  },
  "card_links": [
    {
      "key": "leader_intervene",
      "title": "今日需介入",
      "count": 1,
      "url": "https://.../v2/department-highlights?date=2026-10-13&tab=leader_intervene",
      "item_types": ["TODAY_INTERVENTION"]
    },
    {
      "key": "key_deal_review",
      "title": "重点商机",
      "count": 1,
      "url": "https://.../v2/department-highlights?date=2026-10-13&tab=key_deal_review",
      "item_types": ["KEY_DEAL_REVIEW"]
    }
  ]
}
```

这个例子里没有团队人效、经营趋势，页面就只画两个 tab。形态和销售今日重点列表相同。不要按部门分段，也不要读 `department_id` / `department_name`。`subject_id` 是 oauth id，不要展示。整页 `items` 为空就是空状态。

### 条目字段

报告区块各至多一条，字段和拜访复盘完整报告一样，只给页面正文，**只读**。不要把需介入的采纳、拒绝、评论、挂任务、赞踩画到这些 tab 上，也不要用报告的 `unique_id` 调下面的写入接口（会 404）。没有摘要也没有全文时，这个区块不会出现。

`items.key_deal_review` / `leader_efficiency_insight` / `team_operating_trend`：

| 字段 | 展示 |
| --- | --- |
| `unique_id` | 列表用，只读，不要拿去写入。 |
| `summary` | 摘要，优先展示。空则为 `null`。 |
| `detail_text` | 完整正文，可折叠。空则为 `null`。 |

tab 名用 `card_links[].title`（重点商机 / 团队人效 / 经营趋势）。不要展示写入方带姓名、日期的包装标题。没有 `title`、`item_type`、`evidence`、`severity`、`category`、操作状态、评论、任务。

`items.leader_intervene` 已按 `confidence` 从高到低排好。

| 字段 | 展示 |
| --- | --- |
| `unique_id` | 后续所有写入都用它。不要自己拼，不要改用 `extract_key`。 |
| `extract_key` | 仅后端归并用，页面不用。 |
| `claim` | 卡片主文案。 |
| `leader_action` | 「建议你做」的那一句。 |
| `why` | 原因，可折叠。 |
| `evidence` | 依据，数组。没有时是 `[]`。 |
| `confidence` | 数字或空。只参与排序，不必做成控件。 |
| `related_user_id` / `related_user_name` | 相关销售。名字可能为空，空则显示 id。 |
| `account_id` / `account_name` | 客户。名字查不到时为 `null`。 |
| `opportunity_id` / `opportunity_name` | 商机。同上。 |
| `management_issue` | 管理问题，可空。 |
| `today_window` | 今天要处理的窗口，可空。 |
| `verify_after` | 做完后怎么核对，可空。 |
| `intervention_type` | 小标签，可空，已是小写字符串。 |
| `status` | `PENDING` 待判断，`ADOPTED` 已采纳，`REJECTED` 已拒绝。 |
| `reject_reason` | 拒绝原因。未拒绝时 `null`。 |
| `adopted_todo_id` | 这次采纳关联的那条任务。没有时 `null`。和下面的 `tasks` 不是同一份数据。 |
| `feedback` | `up`、`down` 或 `null`。 |
| `feedback_comment` | 赞踩附言。撤销后为 `null`。 |
| `feedback_at` | 北京时间，无时区，如 `2026-10-13T09:00:00`。未赞踩时 `null`。 |
| `comments` | 评论平铺列表，按时间升序。 |
| `tasks` | 已挂上的任务摘要。 |

同一条建议当天如果被重新抽取，列表仍只有一条。`claim` 等正文是新的，`unique_id`、`status`、赞踩是那条已经采纳或拒绝、不会被作废的行。写入必须用这次返回的 `unique_id`。用户改完赞踩后如果隔一会儿再进来，先重新拉列表，不要沿用进页时缓存的 id。

### 评论

```json
{
  "id": "uuid",
  "reply_to_id": null,
  "author_id": "当前用户 id",
  "author": "张三",
  "content": "先问客户",
  "created_at": "2026-10-13T09:30:00"
}
```

`reply_to_id` 为空是顶层。非空时指向同一条里另一条评论的 `id`，包括回复的回复。接口不组树，页面自己组。已有评论不能改、不能删，这套接口没有对应写操作。

### 任务摘要

```json
{
  "unique_id": "todo-9",
  "title": "确认折扣",
  "owner_id": "oauth-zhang",
  "owner_name": "张三",
  "due_date": "2026-10-13",
  "status": "PENDING",
  "ai_status": "PENDING"
}
```

`status` 优先用任务状态，没有时用 `ai_status`。标题查不到时 `title` 为 `null`，仍展示这条编号。点进任务详情走现有任务页，用 `unique_id`。

`adopted_todo_id` 只表示「采纳时关联了哪条任务」。`tasks` 是挂在这条介入上的全部任务。拒绝后 `adopted_todo_id` 变为 `null`，`tasks` 不变。

---

## 9. 采纳 / 拒绝

只接受 `items.leader_intervene` 里的 `unique_id`。报告条目不要提交。

```http
POST /api/v1/crm/today-highlights/departments/items/decisions
```

```json
{
  "items": [
    {
      "unique_id": "列表里的 unique_id",
      "action": "adopt",
      "adopted_todo_id": null,
      "reject_reason": null
    }
  ]
}
```

| 字段 | 约束 |
| --- | --- |
| `items` | 1 到 100 条。同一次请求里 `unique_id` 不能重复。 |
| `action` | `adopt` 或 `reject`。 |
| `adopted_todo_id` | 仅采纳时可带。必须是 `crm_todos` 里未删除的任务。不带也可以采纳。拒绝时忽略，服务端会清空。 |
| `reject_reason` | 仅拒绝时写入，最长 1000。可以不填。采纳时忽略，服务端会清空。 |

页面上一次只提交当前这一条。批量里只要有一条 `adopted_todo_id` 不存在或已删除，整次请求 **422**，其它条也不会写入。

成功时看每条自己的 `ok`：

```json
{
  "results": [
    {
      "unique_id": "item-1",
      "ok": true,
      "status": "ADOPTED",
      "reject_reason": null,
      "adopted_todo_id": null,
      "feedback": "up",
      "feedback_comment": "判断准确",
      "feedback_at": "2026-10-13T09:00:00"
    },
    { "unique_id": "item-2", "ok": false }
  ]
}
```

`ok: false` 表示这条不存在、已作废，或当前用户不能处理它。其它 `ok: true` 的已经落库。把返回的状态写回对应卡片，赞踩字段保持返回值，不要在前端把赞踩清掉。

| HTTP | 何时 |
| --- | --- |
| 422 | `action` 非法、`unique_id` 空或重复、条数不在 1..100、`adopted_todo_id` 不存在或已删除。 |
| 500 | 其它失败。 |

交互：

- 点「采纳」立刻提交 `action=adopt`，不要带 `adopted_todo_id`，除非用户明确选了已有任务。
- 点「拒绝」先收原因，再提交 `action=reject`。原因可空。
- 已采纳再点拒绝、已拒绝再点采纳，都是再调一次这个接口。

---

## 10. 评论

路径里的 `unique_id` 必须是今日需介入的。报告条目调这个接口会 404。

```http
POST /api/v1/crm/today-highlights/departments/items/{unique_id}/comments
```

```json
{
  "comments": [
    { "content": "先问客户周五前能不能定", "reply_to_id": null }
  ],
  "recipient_user_ids": ["本地 user id"]
}
```

| 字段 | 约束 |
| --- | --- |
| `comments` | 1 到 20 条。页面一次发一条即可。 |
| `content` | 1 到 2000 字。点名要写进正文，例如 `@张三 先问客户周五前能不能定`。名单不落库。 |
| `reply_to_id` | 不回复则省略或 `null`。回复时必须是这条介入里已经返回的评论 `id`。 |
| `author_id` | 不要传。传了就必须等于当前用户，否则 **400**。作者名由服务端从用户档案取。 |
| `recipient_user_ids` | 按 `backend/docs/comment-notify-frontend.md` 组装。本地 user id，不是条目上的 `related_user_id`。回复时把被回复评论的 `author_id` 放进名单，点名人一并放进去。顶层不点名则不传（不推）；点名销售则只放销售。提醒没有跳转，标题是「负责人留言」，文案见该文档第 5.2 节。回复先引用被回复的原文。 |

成功的 `data.comments` 是追加之后的整段线程，直接替换这张卡上的评论列表。

| HTTP | `detail` 含义 |
| --- | --- |
| 404 | 条目不存在或当前用户不能处理。 |
| 400 | `reply_to_id` 对不上、内容为空、`author_id` 不是当前用户。 |

---

## 11. 建任务并写回

只给今日需介入挂任务。本服务不创建任务。顺序：

1. 用现有任务创建接口建好 `crm_todos`，拿到 `unique_id`。
2. 再调用：

```http
POST /api/v1/crm/today-highlights/departments/items/{unique_id}/tasks
```

```json
{ "todo_id": "刚创建的任务 unique_id", "adopt": false }
```

`adopt` 默认按 `false`。勾选「同时采纳这条建议」时传 `true`：任务挂上，并且 `status` 变成 `ADOPTED`，`adopted_todo_id` 写成这条任务。创建失败就不要调用这个接口。

成功：

```json
{
  "unique_id": "item-1",
  "todo_id": "todo-9",
  "adopted": true,
  "status": "ADOPTED"
}
```

返回里没有任务标题。写回成功后重新拉一次列表，用新的 `tasks` 渲染。同一条任务重复写回不会多出一行；`adopt: true` 仍会把采纳状态更新过去。

| HTTP | 何时 |
| --- | --- |
| 404 | 介入条目不存在或不能处理。 |
| 422 | `todo_id` 为空、任务不存在或已删除。此时采纳状态不会变。 |

---

## 12. 赞 / 踩

只给今日需介入。报告条目调这个接口会 404。

```http
POST /api/v1/crm/today-highlights/departments/items/{unique_id}/feedback
```

| 操作 | body |
| --- | --- |
| 赞 | `{ "feedback": "up", "feedback_comment": null }` |
| 踩，可附言 | `{ "feedback": "down", "feedback_comment": "依据不足" }` |
| 撤销 | `{ "feedback": null }` |

`feedback_comment` 最长 500。只有 `up` 或 `down` 时才保存附言。撤销时服务端会把附言和时间一并清空，不用另传。再点一次当前已亮的图标就是撤销。点另一个图标就是切换，附言按这次提交的内容覆盖。

成功的 `data` 与列表上的赞踩字段一致，外加 `unique_id`。写回这张卡即可，不必整页刷新。不要改 `status`。

| HTTP | 何时 |
| --- | --- |
| 404 | 条目不存在或不能处理。 |
| 422 | `feedback` 不是 `up`、`down` 或空。 |

---

## 13. 建议的请求顺序

1. 用 URL 的 `date` 作为 `report_date` 拉列表，不要传 `department_id`。
2. 只按这次返回的 `card_links` 渲染 tab，当前 tab 用 `items[key]`。缺席的 key 不要补空 tab。
3. `leader_intervene` 可以采纳、评论、挂任务、赞踩。报告 tab 名用 `card_links[].title`，正文只渲染 `summary` 和 `detail_text`。
4. 采纳、拒绝、赞踩：用响应里的字段更新这一条。报告不要走这些接口。
5. 评论：用响应里的整段 `comments` 替换。
6. 任务：先走任务服务创建，再写回；写回成功后重新拉列表。
7. 用户重新进入页面，或从后台回到前台时，再拉一次列表，丢掉本地改过的 `unique_id`。

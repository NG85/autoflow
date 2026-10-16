# 今日重点：前端接入

工作台顶上两块，名字用 **我的** 和 **团队**。同一个人可以两块都有，点哪张卡片就打开对应那一块，不要按 `SALES` / `SALES_MANAGER` 只留一块。某一块没有数据时不展示。

| 工作台 | 谁的内容 | 页面 | 列表接口 | 页面上做什么 |
| --- | --- | --- | --- | --- |
| 我的 | 当前用户自己的抽取 | `/v2/today-highlights` | `GET /api/v1/crm/today-highlights/sales` | 立即处理、商机风险、下周待确认都可以采纳/拒绝和赞踩。 |
| 团队 | 当前用户负责的部门，含没有负责人、抬上来的下级部门 | `/v2/department-highlights` | `GET /api/v1/crm/today-highlights/departments` | 对每条需介入做采纳/拒绝、评论、挂任务、赞踩。 |

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

卡片正文只放最紧急的一条「立即处理」。标题始终是小节名，不跳转。正文下面固定一个入口：只有这一条时写「查看」，还有别的时写「更多（N）」，N 是卡片上没展开的条数，都链到 `tab=immediate_today`。商机风险、下周待确认是卡片上的蓝链，多条时文案带条数，例如「商机风险（3）」，分别链到对应 `tab`。

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
2. **商机风险** `open_risks`。没变化的风险服务端已经去掉。
3. **下周待确认** `next_week_confirm`。只在周五出现。

一次请求带回全部 `items`。页面只展示当前 tab 对应的 `items[key]`。`tab` 能对上某个 `card_links.key` 时打开那个 tab；对不上或没传时打开第一个 tab。切换 tab 时把 `tab` 写回地址栏，日期保持不变。

每一条都可以采纳或拒绝，也可以赞或踩。这两块互不影响。销售页没有评论和建任务。

卡片正文按区块不同，底部操作相同：

```text
确认排期                                          待判断
甲客户 · 乙商机
今天不定就延期

为什么：……
依据                                              赞  踩

[ 采纳 ]  [ 拒绝 ]
```

立即处理多写「为什么是今天」「做完的标准」「拖下去的影响」。商机风险在 `risk_change` 为 `resolved` 时，主张前加「已闭环」，并排在该区块最后，其余按置信度从高到低。下周待确认的主文案用 `confirm_prompt`，没有再用 `claim`，可带覆盖范围 `coverage` 和时间 `next_week_when`。

点「采纳」立刻提交。点「拒绝」先收原因，可以空着提交。已经采纳的可以再拒绝，已经拒绝的可以再采纳，后一次覆盖前一次。没有回到「待判断」的操作。赞和踩再点一次当前图标就是撤销，点另一个就是切换。可以给踩写一句附言，最多 500 字。

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
| `source` | 来源，可空。 |
| `risk_status` | 风险状态，小写，可空。 |
| `risk_change` | 风险变化，小写。`resolved` 表示已闭环。 |
| `confirm_prompt` | 下周待确认的主文案。 |
| `coverage` | 下周覆盖范围，小写，可空。 |
| `next_week_when` | 下周时间，可空。 |

三个区块都带下面这些字段。

| 字段 | 含义 |
| --- | --- |
| `status` | `PENDING` 待判断，`ADOPTED` 已采纳，`REJECTED` 已拒绝。 |
| `reject_reason` | 拒绝原因。未拒绝时 `null`。 |
| `adopted_todo_id` | 这次采纳关联的任务。没有时 `null`。本页不建任务，只在采纳时可选带一个已有任务编号。 |
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
- 不写评论，也不在这里建任务或挂任务。采纳可以不带 `adopted_todo_id`。

## 5. 赞 / 踩

```http
POST /api/v1/crm/today-highlights/sales/items/{unique_id}/feedback
```

请求体和成功响应与负责人页的赞踩相同。撤销传 `{ "feedback": null }`。找不到或不是自己的抽取时 **404**，`detail` 为「今日重点条目不存在或无权限」。赞踩不改 `status`。

---

# 部门负责人

登录态沿用现有 CRM 接口。服务端用当前用户判断他负责哪些部门，请求里不要传别人的用户 id。

## 6. 从卡片进页面

飞书卡片链到：

```text
/v2/department-highlights?date=YYYY-MM-DD&tab=leader_intervene
/v2/department-highlights?date=YYYY-MM-DD&department_id={部门id}&tab=leader_intervene
```

| URL 参数 | 含义 |
| --- | --- |
| `date` | 业务日。调接口时改名为 `report_date`。缺省按北京时间当天。 |
| `department_id` | 有则只拉这一个部门。没有则拉当前用户能处理的全部部门。 |
| `tab` | 要打开的 tab，取值是 `card_links[].key`。现在只有 `leader_intervene`。和拜访复盘、销售今日重点的 `tab` 相同。 |

标题始终是小节名，不跳转。正文下面固定一个入口：只有这一条时写「查看」，还有别的时写「更多（N）」，N 是卡片上没展开的条数。当天只有一条、且只涉及一个部门时，这个入口带 `department_id`。还有别的条目，或同一个人负责多个部门时，不带 `department_id`。

页面同样一类一个 tab，tab 来自各部门 `card_links` 的并集，按第一次出现的顺序排，标题用先出现的那条 `title`，`count` 用各类条目加总。现在只有「今日需介入」一个 tab。`tab` 对不上或没传时打开第一个。切换 tab 时把 `tab` 写回地址栏。多个部门时，部门名是这个 tab 里面的分组标题，不是 tab。某个部门在当前 tab 下没有条目，就不渲染这一组。

---

## 7. 页面上三块操作

一张卡上三块互不影响，写的也是不同接口。销售今日重点不要调用这四个地址。

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
GET /api/v1/crm/today-highlights/departments?report_date=2026-10-13&department_id={可选}
```

`report_date`、`department_id` 都可以不传。指定了自己无权处理的部门时，HTTP 200，`departments` 为空数组，不要当成接口失败。

```json
{
  "report_date": "2026-10-13",
  "departments": [
    {
      "department_id": "dept-1",
      "department_name": "华北销售",
      "items": {
        "leader_intervene": []
      },
      "card_links": [
        {
          "key": "leader_intervene",
          "title": "今日需介入",
          "count": 1,
          "url": "https://.../v2/department-highlights?date=2026-10-13&department_id=dept-1&tab=leader_intervene",
          "item_types": ["TODAY_INTERVENTION"]
        }
      ]
    }
  ]
}
```

部门按 `department_name` 排序。某个下级部门没有负责人时，它的条目会出现在上级负责人这份列表里，`department_name` 仍是下级部门自己的名字。按 `departments` 分段渲染即可。

当天没有条目的部门不会出现。整个 `departments` 为空就是空状态。

### 条目字段

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

```http
POST /api/v1/crm/today-highlights/departments/items/{unique_id}/comments
```

```json
{
  "comments": [
    { "content": "先问客户周五前能不能定", "reply_to_id": null }
  ]
}
```

| 字段 | 约束 |
| --- | --- |
| `comments` | 1 到 20 条。页面一次发一条即可。 |
| `content` | 1 到 2000 字。 |
| `reply_to_id` | 不回复则省略或 `null`。回复时必须是这条介入里已经返回的评论 `id`。 |
| `author_id` | 不要传。传了就必须等于当前用户，否则 **400**。作者名由服务端从用户档案取。 |

成功的 `data.comments` 是追加之后的整段线程，直接替换这张卡上的评论列表。

| HTTP | `detail` 含义 |
| --- | --- |
| 404 | 条目不存在或当前用户不能处理。 |
| 400 | `reply_to_id` 对不上、内容为空、`author_id` 不是当前用户。 |

---

## 11. 建任务并写回

本服务不创建任务。顺序：

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

1. 用 URL 的 `date` 作为 `report_date`、`department_id` 原样带上，拉列表。
2. 渲染 `departments[].items.leader_intervene`。
3. 采纳、拒绝、赞踩：用响应里的字段更新这一条。
4. 评论：用响应里的整段 `comments` 替换。
5. 任务：先走任务服务创建，再写回；写回成功后重新拉列表。
6. 用户重新进入页面，或从后台回到前台时，再拉一次列表，丢掉本地改过的 `unique_id`。

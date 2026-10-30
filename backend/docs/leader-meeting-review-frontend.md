# 例会复盘：前端接入

需要三个链接，页面怎么拆参照拜访复盘（列表、`/v2/behavior/{id}/recap`、`/v2/behavior/{id}/extract`），不要求合成一个页面。

| 链接 | 地址 | 对照 |
| --- | --- | --- |
| 列表 | `/v2/business/leader-meeting-review?start_date=&end_date=` | 拜访列表 |
| 单报告 | `/v2/business/leader-meeting-review/{report_id}?date=` | `/v2/behavior/{id}/recap` |
| 抽取 | `/v2/business/leader-meeting-review/{report_id}/extract?tab=&date=` | `/v2/behavior/{id}/extract?tab=` |

登录态沿用现有 CRM 接口。列表和报告正文向 base-api 要。抽取项向本服务要。不要传别人的用户 id。

能看抽取的人有三类：上传人本人、该上传人汇报链上两级内的上级（本部门负责人，以及再上一级）、公司级管理人员（经营层、业务管理员、系统管理员，人不一定在汇报链上）。再往上的领导不靠汇报链看和改。

能改的只有前两类。公司级管理人员只看。查询结果里的 `can_manage` 为 `false` 时，不要画赞踩、采纳、拒绝、建任务、评论和回复。这些写入接口对他们返回 404。

赞踩和采纳记在条目上，一份状态，后一次覆盖前一次。评论是线程，每人改自己写的那条。

| 从哪进来 | 打开 |
| --- | --- |
| 负责人今日重点底部「例会复盘」 | 列表 |
| 列表里点一条 | 单报告 |
| 例会复盘抽取卡的「查看」或「更多（N）」 | 抽取页，并带上对应 `tab` |
| 单报告上的抽取入口 | 抽取页。没指定 `tab` 时打开第一个 |

---

## 1. 从卡片进页面

负责人今日重点底部按钮：

```text
/v2/business/leader-meeting-review?start_date=2026-10-12&end_date=2026-10-18
```

| 参数 | 含义 |
| --- | --- |
| `start_date` | 起始日，`YYYY-MM-DD`，含当天。不必是周一 |
| `end_date` | 结束日，`YYYY-MM-DD`，含当天。不必是周日 |

列表按这两个日期筛报告业务日，任意起止都可以，不要求凑成一整周。今日重点按钮在周二到周日出现，填入本周一到本周日。周一不放这个按钮。地址里没有 `owner_id`，也不要自己补。能看到哪些报告，由列表接口按当前登录人决定：本人上传的，以及下属上传的。

抽取卡上的出口，直接打开抽取页：

```text
/v2/business/leader-meeting-review/<bi_reports.report_id>/extract?tab=management_events&date=2026-10-28
/v2/business/leader-meeting-review/<bi_reports.report_id>/extract?tab=employee_todo_candidates&date=2026-10-28
```

| 参数 | 含义 |
| --- | --- |
| 路径里的 `report_id` | `bi_reports.report_id`。抽取查询、报告详情都用它。不是 `execution_id` |
| `date` | 业务日，只用于展示。调抽取接口不传它，查询按路径里的 `report_id` |
| `tab` | `card_links[].key`。`management_events` 或 `employee_todo_candidates` |

卡片只展开每类置信度最高的一条。一条时出口是「查看」，还有没写上卡片的是「更多（N）」。两个出口都进抽取页，N 不是要再请求的条数。抽取页展示这一份报告的全部抽取。

---

## 2. 列表和单份报告

这两份数据在 base-api，本服务不读 `bi_reports`，也没有列表和正文接口。

不要用 Aldebaran 的 `POST /api/v1/review/list` 和 `POST /api/v1/review/query` 来接列表和单报告。`report_type=leader_meeting_review` 在那套列表里不按上传人收窄，日期筛的是 `created_at`。详情认的是 `execution_id`，和卡片上的 `report_id` 不是同一个字段。

交给 base-api 的条件：

| 场景 | 入参 | 不要传 |
| --- | --- | --- |
| 列表 | URL 的 `start_date`、`end_date`。按报告业务日筛，两端都包含，不必按周对齐 | `owner_id`、别人的用户 id |
| 单份详情 | 列表项或 URL 上的 `report_id` | `execution_id` 代替 `report_id` |

列表项至少要能渲染这些字段，名字以 base-api 实际响应为准：

| 页面要用 | 含义 |
| --- | --- |
| `report_id` | 点进去之后作为单报告路径，也传给抽取接口 |
| 业务日 | 列表上的日期。写到单报告地址时用参数名 `date` |
| 标题 | 没有标题时用业务日 |
| 上传人 | 可空。空着不占一行 |

点一条报告时，打开单报告，不要停在列表上：

```text
/v2/business/leader-meeting-review/<report_id>?date=YYYY-MM-DD
```

单报告只渲染 base-api 详情里的 Markdown，对照拜访复盘的完整报告页。正文为空时提示暂无报告正文。抽取不画在这一页，放一个入口到抽取页。

这段日期里没有报告，就是空状态。加载失败可以重试。

---

## 3. 抽取页：两个 tab

独立页面，对照拜访复盘的 `/extract`。交互对齐团队今日重点里的「今日需介入」（`FeedbackButtons`、`DecisionButtons`、评论、建任务用现有 `createTodos`）。写入地址用下面第 4 到第 7 节，不要调用今日重点的赞踩、采纳、评论、挂任务接口。评论比今日需介入多一个能力：作者可以改自己的正文。这里没有 `recipient_user_ids`，保存评论不会发提醒。

```http
GET /api/v1/crm/leader-meeting-reviews/{report_id}/extracts
```

路径里的 `report_id` 传给这个接口。不传 `report_date`，也不传用户 id。上传人、其汇报链上的上级、公司级管理人员可以看到这些抽取。没有身份、对不上、或还没有抽取时，HTTP 200，`items` 为 `{}`，`card_links` 为 `[]`，`can_manage` 为 `false`。抽取页显示空状态，不要当成没权限。报告正文在单报告页。

成功响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "report_id": "RPT-1",
    "report_date": "2026-10-28",
    "items": {
      "management_events": [],
      "employee_todo_candidates": []
    },
    "card_links": [
      { "key": "management_events", "title": "经营事项", "url": "https://.../v2/business/leader-meeting-review/RPT-1/extract?tab=management_events&date=2026-10-28" },
      { "key": "employee_todo_candidates", "title": "员工待办", "url": "https://.../v2/business/leader-meeting-review/RPT-1/extract?tab=employee_todo_candidates&date=2026-10-28" }
    ],
    "can_manage": true
  }
}
```

`can_manage` 为 `false` 时整页只读：抽取照常展示，不要画第 4 到第 7 节的操作。上传人或汇报链上级打开时为 `true`。

tab 不写死。按 `card_links` 的顺序渲染，文案用 `title`，条数用 `items[key].length`。没有的类型不出现，不要留空 tab。`tab` 能对上某个 `key` 时打开它；对不上或没传时打开第一个，并把 `tab` 写回地址栏。切换 tab 时只改 `tab`，路径里的 `report_id` 和 `date` 保持不变。

服务端已按 `confidence` 从高到低排好。页面不要再排。

### 经营事项 `management_events`

赞踩和评论都要。不要画采纳、拒绝、建任务。

```text
甲客户 · 关键人
路径要调整

由区域负责人协助引荐                          赞  踩
```

| 字段 | 展示 |
| --- | --- |
| `unique_id` | 后续写入用。重新进页要再拉一次，不要缓存 |
| `business_object` | 标题前半。空则不写 |
| `management_issue` | 标题后半，中间用 ` · `。两个都空时退回 `claim` |
| `change_summary` | 标题下面一行。空则用 `claim` |
| `leader_management` | 再空一行。空则不写 |
| `claim` | 上面两处的兜底，不要单独再画一行 |
| `confidence` | 只参与服务端排序，不必做成控件 |
| `status` | 行上的状态。这类不提供采纳或拒绝，不要画成「待判断 / 已采纳」 |
| `feedback` | `up`、`down` 或 `null` |
| `feedback_comment` | 赞踩附言。撤销后为 `null` |
| `feedback_at` | 北京时间，无时区。未赞踩时 `null` |
| `comments` | 评论线程，没有时是 `[]`。见第 7 节 |

### 员工待办 `employee_todo_candidates`

赞踩、采纳、拒绝、建任务、评论都要。

```text
王磊                                              待判断
确认预算审批流程
截止 本周五
验证：拿到参与人                                  赞  踩

[ 采纳 ]  [ 拒绝 ]  [ 建任务 ]
```

| 字段 | 展示 |
| --- | --- |
| `unique_id` | 后续写入用 |
| `assignee_name` | 标题。空则不写姓名 |
| `action` | 标题下面一行。空则用 `claim` |
| `due_text` | `截止 {due_text}`。空则不写。这是原文，例如「本周五」，不要解析成日期 |
| `verification_criteria` | `验证：{verification_criteria}`。空则不写 |
| `claim` | `action` 的兜底 |
| `status` | `PENDING` 待判断，`ADOPTED` 已采纳，`REJECTED` 已拒绝 |
| `reject_reason` | 拒绝原因。未拒绝时 `null` |
| `adopted_todo_id` | 这次采纳关联的那条任务。没有时 `null`。已挂上的全部任务看 `tasks` |
| `feedback` | `up`、`down` 或 `null` |
| `feedback_comment` | 赞踩附言。撤销后为 `null` |
| `feedback_at` | 北京时间，无时区。未赞踩时 `null` |
| `tasks` | 已挂上的任务摘要，没有时是 `[]`。字段与今日需介入的任务摘要相同 |
| `comments` | 评论线程，没有时是 `[]`。见第 7 节 |
| `confidence` | 不必做成控件 |

拒绝后 `adopted_todo_id` 变为 `null`，`tasks` 不变。点进任务详情走现有任务页，用 `unique_id`。

---

## 4. 赞 / 踩

经营事项和员工待办都走这个地址。不要调今日重点的赞踩接口。

```http
POST /api/v1/crm/leader-meeting-reviews/items/{unique_id}/feedback
```

| 操作 | body |
| --- | --- |
| 赞 | `{ "feedback": "up", "feedback_comment": null }` |
| 踩，可附言 | `{ "feedback": "down", "feedback_comment": "依据不足" }` |
| 撤销 | `{ "feedback": null }` |

`feedback_comment` 最长 500。只有 `up` 或 `down` 时才保存附言。撤销会把附言和时间一并清空。再点一次当前已亮的图标就是撤销。点另一个图标就是切换。赞踩不改 `status`。

成功的 `data` 含 `unique_id`、`feedback`、`feedback_comment`、`feedback_at`。写回这一条即可。

| HTTP | 何时 |
| --- | --- |
| 404 | 条目不存在、已作废，或当前登录人不是上传人、也不是其汇报链上的上级。`detail` 为「例会复盘条目不存在或无权限」 |
| 422 | `feedback` 不是 `up`、`down` 或空 |

## 5. 采纳 / 拒绝

只接受员工待办的 `unique_id`。经营事项放进来时，这一条 `ok` 为 false，其它条照常写入。

```http
POST /api/v1/crm/leader-meeting-reviews/items/decisions
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
| `items` | 1 到 100 条。同一次请求里 `unique_id` 不能重复 |
| `action` | `adopt` 或 `reject` |
| `adopted_todo_id` | 仅采纳时可带。必须是未删除的任务。不带也可以采纳。拒绝时忽略，服务端会清空 |
| `reject_reason` | 仅拒绝时写入，最长 1000。可以不填。采纳时忽略，服务端会清空 |

页面上一次只提交当前这一条。批量里只要有一条 `adopted_todo_id` 不存在或已删除，整次请求 **422**，其它条也不会写入。

成功时看每条自己的 `ok`。`ok: true` 的 `data.results` 里带 `status`、`reject_reason`、`adopted_todo_id` 和赞踩字段。把返回的状态写回对应卡片，不要在前端把赞踩清掉。`ok: false` 表示这条不是上传人或其汇报链上级的员工待办，或已经作废。经营事项也是 `ok: false`。

点「采纳」立刻提交 `action=adopt`。点「拒绝」先收原因，再提交 `action=reject`，原因可空。已采纳再拒绝、已拒绝再采纳，都是再调一次。没有改回「待判断」。

## 6. 建任务并写回

只给员工待办挂任务。经营事项调这个接口会 404。本服务不创建任务。

1. 用现有 `createTodos` 建好 `crm_todos`，拿到 `unique_id`。
2. 再调用：

```http
POST /api/v1/crm/leader-meeting-reviews/items/{unique_id}/tasks
```

```json
{ "todo_id": "刚创建的任务 unique_id", "adopt": false }
```

`adopt` 默认 `false`。勾选「同时采纳这条待办」时传 `true`：任务挂上，并且 `status` 变成 `ADOPTED`，`adopted_todo_id` 写成这条任务。创建失败就不要调用这个接口。

成功的 `data` 含 `unique_id`、`todo_id`、`adopted`、`status`。返回里没有任务标题。写回成功后重新拉一次抽取，用新的 `tasks` 渲染。同一条任务重复写回不会多出一行。

| HTTP | 何时 |
| --- | --- |
| 404 | 不是上传人或其汇报链上级的员工待办。经营事项也是 404 |
| 422 | `todo_id` 为空、任务不存在或已删除。此时采纳状态不会变 |

## 7. 评论

经营事项和员工待办都走这个地址。不要调今日需介入的评论接口，也不要传 `recipient_user_ids`。这个接口不发评论提醒。

能看到这条抽取的人（上传人，或其汇报链上的上级）都可以写新评论、回复任意一条已有评论。`author_id` 等于当前登录用户时才显示「修改」。不能改别人的正文，也不能删除。

```http
POST /api/v1/crm/leader-meeting-reviews/items/{unique_id}/comments
```

新评论、回复、修改自己的正文可以放在同一次请求里：

```json
{
  "comments": [
    { "content": "先确认范围" },
    { "content": "周五前给结论", "reply_to_id": "被回复评论的 id" },
    { "id": "自己那条评论的 id", "content": "改后的正文" }
  ]
}
```

| 字段 | 约束 |
| --- | --- |
| `comments` | 1 到 20 条 |
| `content` | 不能为空白，最长 2000 |
| `reply_to_id` | 回复时填写，必须是本条已有评论的 `id`，包括回复的回复。新评论和修改都不填 |
| `id` | 只在修改时填写。不存在，或 `author_id` 不是当前用户，整次请求失败，本次都不会写入 |
| `author_id` | 可不传。传了必须是当前登录用户的本地 user id |

查询和写入返回的每条评论：

```json
{
  "id": "评论 id",
  "reply_to_id": null,
  "author_id": "本地 user id",
  "author": "甲",
  "content": "先确认范围",
  "created_at": "2026-10-30T10:00:00"
}
```

| 字段 | 展示 |
| --- | --- |
| `id` | 回复和修改时回传。修改按钮只在它的 `author_id` 是当前用户时出现 |
| `reply_to_id` | 空是顶层。非空指向同一条抽取里另一条评论的 `id`。接口不组树，页面自己组 |
| `author_id` | 本地 user id。用来判断能不能修改，不要展示 |
| `author` | 展示名 |
| `content` | 正文 |
| `created_at` | 北京时间，无时区。按这个升序 |

成功的 `data` 含 `unique_id` 和保存后的整段 `comments`。用它替换这一条上的 `comments`，不要只把本次提交的那几条接在后面。

| HTTP | 何时 |
| --- | --- |
| 400 | 正文为空、`author_id` 不是当前用户、`reply_to_id` 对不上，或在改别人的评论。`detail` 为具体原因 |
| 404 | 条目不存在、已作废，或当前登录人不是上传人、也不是其汇报链上的上级。`detail` 为「例会复盘条目不存在或无权限」 |

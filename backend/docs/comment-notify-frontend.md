# 评论提醒：前端接入

拜访记录、周跟进、今日需介入、例会复盘四个写评论接口共用一个请求字段 `recipient_user_ids`。服务端规则是：**名单非空就只发给这些人；不传或 `[]` 才走默认接收人。** 名单不会和默认人合并。

回复时前端把被回复评论的原作者放进名单，点名人一并放进去。这样回复也走指定人链路，不用依赖服务端默认。

销售今日重点没有评论，不要调这些接口。

---

## 1. 名单怎么组

`recipient_user_ids` 是本地 `users.id`（评论条目上的 `author_id` 就是这种）。不要用 oauth id，也不要用今日需介入条目上的 `related_user_id`。

一次请求只发一条评论。名单只用于这一次推送，**不落库**。之后拉评论看不到当时点了谁。

要页面刷新后还能显示 @，前端自己把点名写进 `content`，例如 `@张三 先问客户周五前能不能定`。渲染用正文里的 `@姓名`，推送用旁边的 `recipient_user_ids`。被回复的原作者走 `reply_to_id`，不必再写进正文。

```text
名单 = 去重后的：
  - 回复时：被回复评论的 author_id
  - 用户点名的人
```

当前登录人可以放进去，服务端会跳过，不会自己提醒自己。`type=task` 不推送，名单无意义。

| 操作 | 要不要传 `recipient_user_ids` | 名单里放谁 |
| --- | --- | --- |
| 拜访 / 周跟进，顶层，不点名 | 不传或 `[]` | 服务端发给记录人 / 负责人 |
| 拜访 / 周跟进，顶层，点名了 | 传 | 记录人 / 负责人 + 点名人。不放记录人 / 负责人，他们就收不到 |
| 今日需介入，顶层，不点名 | 不传或 `[]` | 不推。这是负责人记在自己卡片上的处理说明 |
| 今日需介入，顶层，点名销售 | 传 | 被点名的销售（本地 user id） |
| 例会复盘，顶层，不点名 | 不传或 `[]` | 上传人。抽取响应里的 `uploader_user_id` |
| 例会复盘，顶层，点名了 | 传 | 点名人。要同时通知上传人，把 `uploader_user_id` 放进去 |
| 任意场景，回复 | 传 | **被回复评论的 `author_id`** + 点名人 |

回复不带原作者、只带点名人时，原作者收不到。

---

## 2. 组装示例

回复，并点名一个人：

```js
const mentioned = [{ id: "019ab89d-ab72-7d69-b61e-1ede7450c0c3", name: "张三" }];
const parent = comments.find((item) => item.id === replyToId);
const recipient_user_ids = [
  parent?.author_id,
  ...mentioned.map((item) => item.id),
].filter(Boolean);
const mentionPrefix = mentioned.map((item) => `@${item.name}`).join(" ");
const content = mentionPrefix
  ? `${mentionPrefix} ${plainText}`
  : plainText;
```

顶层评论、拜访里点名协同人，仍要通知记录人：

```js
const recipient_user_ids = [record.recorder_id, ...mentioned].filter(Boolean);
```

今日需介入顶层只通知被点名的销售，不要把负责人自己放进名单（放了也会被跳过）。不要拿 `related_user_id` 当接收人。

---

## 3. 写接口

鉴权与现有 CRM 接口相同。成功体都是 `{ "code": 0, "message": "success", "data": ... }`。

### 3.1 拜访记录

```http
POST /api/v1/crm/visit_records/{record_id}/comments
```

```json
{
  "comments": [
    {
      "author_id": "当前登录用户 id",
      "author": "张三",
      "content": "@李四 确认下周技术方案评审时间",
      "type": "comment",
      "reply_to_id": null
    }
  ],
  "recipient_user_ids": ["记录人或点名人的本地 user id"]
}
```

- 每条 `author_id` 必须是当前登录用户，否则 **400**。
- 无 `id` 追加；带已有 `id` 且是本人评论则只改 `content`，会再推一次，标题带「编辑过」。
- `reply_to_id` 必须是这条记录里已有评论的 `id`。
- 默认接收人：顶层 = 记录人 `recorder_id`，回复 = 被回复 `author_id`（前端按第 1 节带上后，不再依赖这个默认）。

### 3.2 周跟进

```http
POST /api/v1/crm/weekly-followup/entities/{entity_id}/comments
```

请求体与拜访相同，字段名也是 `comments` + `recipient_user_ids`。默认接收人：顶层 = 该实体负责人，回复 = 被回复 `author_id`。

### 3.3 今日需介入

只出现在负责人今日重点的「今日需介入」。报告条目、销售今日重点不要调。

```http
POST /api/v1/crm/today-highlights/departments/items/{unique_id}/comments
```

```json
{
  "comments": [
    {
      "content": "@张三 先问客户周五前能不能定",
      "reply_to_id": null
    }
  ],
  "recipient_user_ids": ["被点名销售的本地 user id"]
}
```

| 字段 | 约束 |
| --- | --- |
| `comments` | 1 到 20 条。页面一次发一条。 |
| `content` | 1 到 2000 字，不能空。点了名就把 `@姓名` 写在正文里，否则刷新后看不到。 |
| `reply_to_id` | 不回复则省略或 `null`。回复时必须是这条介入已返回的评论 `id`。 |
| `author_id` | 不要传。传了就必须等于当前用户，否则 **400**。作者名由服务端从档案取。 |
| `recipient_user_ids` | 按第 1 节组装。顶层不点名就不传。 |

成功的 `data.comments` 是追加后的整段线程，直接替换这张卡上的评论列表。不能编辑已有评论。

### 3.4 例会复盘

经营事项和员工待办都能评论。公司级管理人员只看，不要调这个接口。

```http
POST /api/v1/crm/leader-meeting-reviews/items/{unique_id}/comments
```

请求体与今日需介入相同，另加可选的评论 `id`：带已有 `id` 且是本人评论则只改 `content`，会再推一次，标题带「编辑过」。

默认接收人：顶层 = 上传人 `uploader_user_id`，回复 = 被回复 `author_id`。提醒有抽取页链接，按钮文案是经营对象或员工待办摘要。推送失败不影响评论已经保存。

| HTTP | `detail` |
| --- | --- |
| 404 | 条目不存在，或当前用户不能处理。 |
| 400 | `reply_to_id` 对不上、内容为空、`author_id` 不是当前用户。 |

---

## 4. 飞书 / 钉钉卡片

推送失败不影响评论已经保存。自己给自己不推。

| 场景 | 标题 | 正文 | 按钮 |
| --- | --- | --- | --- |
| 拜访顶层 | `{作者}评论了你的跟进记录` | 引用评论 | 客户 商机，打开拜访详情侧栏 |
| 拜访回复 | `{作者}回复了你的评论` | 同上 | 同上 |
| 周跟进顶层 | `{作者}评论了你的周跟进总结` | 周次 + 引用 | 客户 商机，打开周跟进详情侧栏 |
| 周跟进回复 | `{作者}回复了你的评论` | 同上 | 同上 |
| 今日需介入顶层 | `{作者}给你留了评论` | 客户 商机（纯文本）+ 引用 | **没有**。销售进不了负责人今日重点页，这条评论也不在销售自己的今日重点上 |
| 今日需介入回复 | `{作者}回复了你的评论` | 同上 | **没有** |
| 例会复盘顶层 | `{作者}评论了例会复盘` | 抽取摘要链接 + 引用 | 打开抽取页对应 tab |
| 例会复盘回复 | `{作者}回复了你的评论` | 同上 | 同上 |

编辑过的拜访 / 周跟进标题末尾带 `（MM-DD HH:mm 编辑过）`，飞书标题为橙色。例会复盘修改后再推一次，标题末尾是 `（编辑过）`。

---

## 5. 对接检查

- 回复请求里一定有被回复评论的 `author_id`。
- 点名人是本地 user id，不是 oauth id，也不是 `related_user_id`。
- 拜访 / 周跟进顶层点名时，名单里仍有记录人 / 负责人。
- 今日需介入顶层不点名不传名单；点名了只放销售。
- 例会复盘顶层点名时，要通知上传人就放上 `uploader_user_id`。回复放上被回复评论的 `author_id`。
- 点名写进 `content`（`@姓名`），不要指望服务端另存一份名单。
- 今日需介入的提醒没有跳转，页面上不必为这条推送准备落地页。

# 技术规格说明书：跟进评论可编辑

| 要素 | 内容 |
| --- | --- |
| 作者 | nagao |
| 时间 | 2026-10-23 |
| 变更窗口 | 2026-10-23 |
| 前置说明书 | `tsd/2026-10-19-release.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/visit_records、crm/weekly_followup、notification |

本文件描述 `aafc8b9d` 的 backend 实现。用户可见行为见 `fsd/2026-10-23-comment-edit.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 追加与编辑合并 | `backend/app/utils/crm_comments.py` 的 `merge_append_crm_comments` |
| 拜访评论落库 | `backend/app/repositories/visit_record.py` 的 `update_visit_record_comments` |
| 拜访 HTTP 与推送 | `backend/app/api/routes/crm/visit_records/router.py` |
| 周跟进 HTTP、推送与读出 | `backend/app/api/routes/crm/weekly_followup/router.py` |
| 评论模型 | `backend/app/api/routes/crm/models.py` 的 `CRMComment` |
| 文本消息发送 | `platform_notification_service.send_visit_record_comment_notification`、`send_weekly_followup_comment_notification` |
| 合并单测 | `backend/tests/test_crm_comments_merge.py` |

拜访评论存在 `crm_sales_visit_records.comments`。周跟进评论存在 `crm_weekly_followup_entity_summaries.comments`。两者都是 JSON 数组，编辑在原对象上改 `content` 并写入 `updated_at`。

## 2. 合并

`merge_append_crm_comments(..., allow_edit=False)` 的追加行为与改前相同，返回值改为 `(merged, appended, edited)`。`allow_edit=False` 时 `edited` 恒为空。拜访和周跟进都传入 `allow_edit=True`。

命中规则：

- 请求条目的 `id` 能在已有数组里找到，且该条 `author_id` 等于当前用户：写入 `content` 和 `updated_at`（北京时间 ISO），其余键不动。该条进入 `edited`，不进入 `appended`。
- 该条作者不是当前用户：抛 `CRMCommentValidationError`，文案「只能编辑本人的评论」。调用方转成 400，且发生在赋值和提交之前。
- 没有 `id`，或 `id` 不存在：走原追加。`type=comment` 生成新 UUID，忽略客户端传入的 `id`。`type=task` 沿用客户端 `id`，不传则不生成。

历史 `type=comment` 仍在合并时懒补 `id`。历史 `type=task` 没有 `id` 时不补，因此没有 `id` 的旧任务不能被这条编辑路径命中。

## 3. 接口

两个 POST 的路径、登录要求和请求体字段不变。`author_id` 仍须等于当前用户，否则 400。

拜访：`POST /crm/visit_records/{record_id}/comments`。可见性仍走 `sales:follow_up:view` 的单条 check，无权限或不存在返回 404。成功体仍是 `{ "code": 0, "message": "success", "data": { "comments": [] } }`，`comments` 为库内 JSON。

周跟进：`POST /crm/weekly-followup/entities/{entity_id}/comments`。成功体仍是实体行。负责人参与度仍只在 `appended` 非空且当前用户是团队负责人时写 `commented_at`。

## 4. 推送

推送循环改为只遍历 `appended` 和 `edited`，不再遍历原始请求体。因此没落库的条目不会推送。

`edited` 与 `appended` 使用同一收件人：

- 拜访顶层给 `recorder_id`，回复给 `reply_to_id` 指向的 `author_id`。
- 周跟进顶层给 `owner_user_id`，回复给被回复评论的 `author_id`。
- `type` 不是 `comment`、收件人为空或等于当前用户时跳过。

编辑消息在首行末尾追加 `（MM-DD HH:mm 编辑过）`。时间来自刚写入的 `updated_at`。评论预览仍截断到 200 字。发送仍是新的文本消息，不调用飞书编辑消息接口，也不保存 `message_id`。

## 5. 读出

拜访详情和列表把 `comments` 当作 JSON 原样返回，有 `updated_at` 就会出现。

周跟进经 `_to_comments` 装成 `CRMComment`。`updated_at` 能解析才填入。模型序列化时该字段为空则从结果里去掉，未编辑的评论响应与改前字段一致。解析失败只丢掉这个时间，不丢掉整条评论。

## 6. 任务 id

`allow_edit=True` 时，本人已有 `id` 的 `type=task` 会在「任务 id 已存在」之前被当成编辑。结果是更新正文，不推送。这只发生在拜访记录和周跟进。其他调用方若保持 `allow_edit` 默认值，重复任务 `id` 仍返回 400。

## 7. 测试

`backend/tests/test_crm_comments_merge.py` 覆盖：只改本人正文、拒绝改别人、未知 `id` 仍追加、关闭编辑时旧 `id` 仍追加、本人任务可更新，以及 `updated_at` 为空时不出现在序列化结果里。推送文案没有单测。

# 技术规格说明书：评论提醒指定接收人

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-27 |
| 变更窗口 | 2026-10-27 |
| 前置说明书 | `tsd/2026-10-23-comment-edit.md`、`tsd/2026-10-27-comment-im-applink.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/visit_records、crm/weekly_followup、crm/today_highlights、notification |

本文件描述写评论指定接收人的 backend 实现。用户可见行为见 `fsd/2026-10-27-comment-notify.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 名单解析、跳过自己、去重 | `resolve_comment_notify_recipients` |
| 请求体可选字段 | `VisitRecordCommentsUpdate`、`SaveWeeklyFollowupCommentsIn`、`InterventionCommentsUpdate` 的 `recipient_user_ids` |
| 拜访推送循环 | `update_visit_record_comments` |
| 周跟进推送循环 | `save_weekly_followup_comments` |
| 今日需介入保存后推送 | `notify_item_comments`，由 `save_department_highlight_comments` 调用 |
| 今日需介入发送 | `send_today_highlight_comment_notification`，复用评论卡片布局 |
| 单测 | `backend/tests/test_crm_comments_merge.py`、`backend/tests/test_leader_intervention_actions.py`、`backend/tests/test_comment_notification_applink.py` |

## 2. 收件人

`specified` 去空后非空则用名单，否则用这条评论的默认人。比较自己时把 UUID 有无连字符当成同一个人。结果按名单原顺序，去重。

| 场景 | 默认人 |
| --- | --- |
| 拜访顶层 | `recorder_id` |
| 周跟进顶层 | `owner_user_id` |
| 今日需介入顶层 | 空字符串（不推） |
| 任意回复 | 父评论 `author_id` |

每人仍走原来的发送函数。文案仍按顶层 / 回复区分，不随指定名单改写。

## 3. 今日需介入文案

不拼 markdown 链接。客户名加商机名（没有则用 `claim`）作为普通上下文行。发送走已有 `_layout_comment_style_message`，没有整行链接时 `footer_buttons` 为空。

## 4. 测试

- 未指定或空列表：拜访 / 周跟进回落到默认人；今日需介入顶层不推，回复发给父评论作者。
- 指定名单覆盖默认人，跳过自己和重复 id。
- 三个请求体都接受 `recipient_user_ids`。
- 今日需介入发送失败不影响保存。
- 今日需介入飞书卡片有标题和引用，没有按钮和页面 URL。

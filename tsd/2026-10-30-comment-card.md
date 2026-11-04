# 技术规格说明书：评论卡片与作者名

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-30 |
| 变更窗口 | 2026-10-30 |
| 前置说明书 | `tsd/2026-10-27-comment-notify.md`、`tsd/2026-10-29-leader-meeting-review-card.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/visit_records、crm/weekly_followup、crm/today_highlights、crm/leader_meeting_reviews |

本文件描述评论作者补名、追加或修改，以及提醒文案的 backend 实现。用户可见行为见 `fsd/2026-10-30-comment-card.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 作者补名 | `fill_missing_comment_authors`；姓名来自 `UserProfileRepo.get_names_by_user_ids`，不过滤 `is_active` |
| 追加或修改、`author_id` 校验 | `append_or_edit_crm_comments`、`incoming_author_matches_current_user` |
| 对象名截断 | `comment_subject_label`，默认 40 字；两段都有时按段均分，缺的段不占位 |
| 进得去页面的标题和首句 | `comment_notify_lines` |
| 今日需介入留言 | `comment_note_lines`，事项最多 80 字 |
| 引用原文与新正文 | `comment_message_tail`；回复时原文以「评论：」起行，新正文是下一行纯文本 |
| 引用顺序 | `_layout_comment_style_message` 按原文顺序输出，引用不再被挪到正文末尾 |
| 拜访 / 周跟进发送 | `update_visit_record_comments`、`save_weekly_followup_comments` |
| 今日需介入 / 例会复盘发送 | `notify_item_comments`、`notify_meeting_review_comments` |

查询补名的调用点：拜访转响应之后、周跟进评论查询、`load_threads` 与主体评论保存后的返回。补名失败只记日志，`author` 保持空白。

## 2. 写入

`author_id` 空白视为当前用户。非空必须与 `str(current_user_id)` 完全一致，连字符形式不另做归一。不一致抛 `CRMCommentValidationError`，HTTP 400，本次评论都不写入。

`append_or_edit_crm_comments` 保持原顺序。未知 `id` 的评论追加，服务端生成新 id。命中本人评论只更新 `content` 和 `updated_at`。命中他人评论拒绝。任务条目仍保留客户端 id，重复任务 id 400。不再拆分「我的 / 别人的」，也不再重排。

## 3. 文案分支

`comment_notify_lines` 用 `_user_id_key` 比较收件人与被回复作者（UUID 有无连字符视为同一人）。相等且是回复为「评论回复」，相等且是顶层为「新评论」，否则为「评论提及」。对象名与场景名相同时不重复。

今日需介入全部走 `comment_note_lines`，不附链接。例会复盘全部走 `comment_notify_lines`，有抽取页地址就附上按钮，不按收件人能否打开页面分支。

`include_parent` 为真的条件是这条评论有 `reply_to_id`，与收件人是原作者还是被点名无关。父评论正文取保存后的线程。

## 4. 测试

- `backend/tests/test_crm_comments_merge.py`：追加或修改、作者补名、对象名截断、留言首句、回复原文。
- `backend/tests/test_leader_intervention_actions.py`：今日需介入留言没有场景名和链接；回复带原文。
- `backend/tests/test_leader_meeting_review_actions.py`：上传人和被点名人都是短标题加抽取页链接；回复带原文。
- `backend/tests/test_comment_notification_applink.py`：引用出现在新正文之前，原有按钮和侧栏链接不变。

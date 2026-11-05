# 技术规格说明书：例会复盘卡片发给负责人和提交人

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-05 |
| 变更窗口 | 2026-11-05 |
| 前置说明书 | `tsd/2026-10-29-leader-meeting-review-card.md` |
| 关联服务 | backend；Aldebaran |
| 关联功能模块 | notification、crm/leader_meeting_review |

本文件描述双收件人发卡。用户可见行为见 `fsd/2026-11-05-leader-meeting-review-two-recipients.md`。抽取查询、权限和页面链接仍见前置说明书。没有新增表或迁移。

## 1. 请求

`LeaderMeetingReviewCardPushRequest` 增加可选 `creator_id`。`report_id`、`owner_id` 仍必填。不传 `creator_id` 时行为与只发给 `owner_id` 相同。

## 2. 发送

`handle_leader_meeting_review_card` 仍用 `load_extract_rows(report_id, owner_id)` 读取抽取，不按 `creator_id` 再查一遍。收件人是去重后的 `owner_id`、`creator_id`。每人走原来的 `user_profile_repo.get_by_oauth_user_id` 和 `deliver_card`。

有一人成功则 `pushed=true`、`reason=null`。都没有 open_id 时 `reason=no_open_id`。没有成功且至少一次发送失败时 `reason=send_failed`。没有抽取行时 `reason=empty_extracts`，此时不查收件人。响应在带了 `creator_id` 时回传该字段。

## 3. 提交人可改

写入仍走 `load_meeting_review_item` / `_matches`。负责人本人和其汇报链上级沿用原判断。对不上时再读 `bi_reports.created_by`（`source_report_unique_id` = `report_id`，取最新一行）。当前用户的 oauth id 或 open_id 与这一列相同即可改。读不到提交人时不放行。`can_manage` 对提交人同样为 true。

## 4. 代码落点

| 职责 | 位置 |
| --- | --- |
| 可选 `creator_id` | `backend/app/api/routes/notification_schemas.py` |
| 把 `creator_id` 传给处理函数 | `backend/app/api/routes/notification.py` |
| 去重收件人并逐个发送 | `backend/app/services/leader_meeting_review_card.py` |
| 提交人按 `bi_reports.created_by` 获得修改权限 | `backend/app/services/leader_meeting_review_actions.py` |
| 单测 | `backend/tests/test_leader_meeting_review_card_push.py`、`backend/tests/test_leader_meeting_review_actions.py` |

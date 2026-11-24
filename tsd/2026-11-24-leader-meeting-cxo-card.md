# 技术规格说明书：例会管理评价卡片发给 CXO

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-24 |
| 变更窗口 | 2026-11-23 ～ 2026-11-24 |
| 前置说明书 | `tsd/2026-11-20-leader-meeting-review-summary-b64.md` |
| 对应 FSD | `fsd/2026-11-24-leader-meeting-cxo-card.md` |
| 关联服务 | backend；Aldebaran |
| 关联功能模块 | notification、crm/leader_meeting_review |

用户可见行为见对应 FSD。没有新表，没有迁移，没有新权限码。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 推送开关 | `LEADER_MEETING_CXO_CARD_PUSH_ENABLED`，默认 `false` |
| 请求体 | `backend/app/api/routes/notification_schemas.py` 的 `LeaderMeetingReviewCxoCardPushRequest` |
| 推送分支 | `backend/app/api/routes/notification.py` |
| 读洞察、读抽取、组装、发送 | `backend/app/services/leader_meeting_review_cxo_card.py` |
| 收件人 | `company_today_highlights.resolve_company_highlight_recipients`。策略名单优先，否则 `notification:daily_report_company:receive`。请求里的 `recipient_user_ids` 是本地 user id，只做交集 |
| 三端发送 | `leader_meeting_review_card.deliver_card`。组装结果自带标题、链接和企微文案时用这些字段。企微有全文时跳转「完整评价报告」「原复盘报告」，没有全文时主跳转用抽取按钮文案 |
| 页面地址 | `backend/app/utils/push_page_urls.py` |
| 抽取查询 | `GET /api/v1/crm/leader-meeting-reviews/cxo/{insight_id}/extracts` |
| 单测 | `backend/tests/test_leader_meeting_review_cxo_card.py` |

## 2. 数据

洞察：`crm_entity_insight.unique_id` 在请求的 `insight_ids` 里，`entity_type=USER`，`insight_type=LEADER_MEETING_BEHAVIOR_EVAL`，`is_deleted=0`，`expires_at` 为空或晚于当前 UTC。按 `generated_at`、`id` 取最新一条。卡片正文用 `summary`。有 `detail_text` 且请求带了 `source_report_id` 才放「完整评价报告」，地址是原例会报告页加 `tab=management`，不带日期。`period_key` 是这场会的报告 id，不当成日期。报告页由 base-api 用同一套条件查询，但入口是 `period_key` 等于这场会的 `report_id`，`entity_id` 是原例会负责人的 oauth id；响应带 `unique_id`，正文用 `detail_text`。卡片标题上的日期只来自请求里的 `report_date`。不做 `__b64__:` 解码。负责人姓名用洞察 `entity_id` 查 `user_profiles.name`。查不到则不写姓名。

抽取：`crm_skill_extract_items.source_report_unique_id` 等于这条洞察，`audience=cxo`，未删除且非 `SUPERSEDED`。类型只有 `LEADER_BEHAVIOR_COACHING`。

抽取查询的响应含 `insight_id`、`report_date`、`items`、`card_links`、`can_manage`、`uploader_user_id`，不含 `detail_text`。条目带赞踩、采纳和 `tasks`、`comments`。能看的人 `can_manage` 为 true，`uploader_user_id` 为 null。观众用 `viewer_is_company_highlight_recipient`：策略名单优先，否则公司日报接收人。不按公司级角色放行，也不因当前用户就是被评价人而拒绝。无权时 HTTP 404，`detail` 为「管理评价抽取不存在或无权限」。写入仍是 `/crm/leader-meeting-reviews/items/` 下的 feedback、decisions、comments、tasks。这一类在 `todo_only` 时也放行。评论提醒链到管理评价抽取页，不默认发给被评价负责人。

## 3. 返回

推送 HTTP 200。关闭开关时 `reason` 为 `feature_disabled`，且不解析收件人、不读洞察。其余 `reason` 为 `no_recipients`、`insight_not_found`、`empty_extracts`、`no_open_id`、`send_failed` 之一，发出时为空。不调用演示旁路和运维抄送。

## 4. 不改的路径

`leader_meeting_review_card` 的收件人、会议总结和两类抽取不改。负责人抽取查询再排除 `audience=cxo`。不提供管理评价全文查询。管理评价抽取的写入复用上述四个接口，不另开路由。

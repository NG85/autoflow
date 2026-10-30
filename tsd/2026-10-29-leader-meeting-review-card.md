# 例会复盘卡片

## 入口

- `POST /api/v1/notification/push`，`type=leader_meeting_review_card`。`report_id`、`owner_id` 必填，`report_date` 可选。缺字段返回 422。
- `GET /api/v1/crm/leader-meeting-reviews/{report_id}/extracts`。登录态与今日重点相同。

## 抽取

只读 `crm_skill_extract_items`。

过滤：`source_report_unique_id=report_id`，`subject_type=USER`，`is_deleted=0`，`status!=SUPERSEDED`，`item_type` 为 `MANAGEMENT_EVENT` 或 `EMPLOYEE_TODO_CANDIDATE`。不按 `report_date` 再筛。调用人是这些行的 `subject_id`，或该上传人 OAuth 汇报链上两级内的上级（按 open_id 对齐，`max_levels=2`：本部门负责人，以及再上一级），或公司级角色 `BIZ_ADMIN`、`EXECUTIVE`、`SYS_ADMIN`。例会复盘还没有 data-scope 实体和策略，公司级先不查 `global`。

查询返回两类的全部条目，按 `payload.confidence` 从高到低。分段标题固定「经营事项」「员工待办」，key 为 `management_events`、`employee_todo_candidates`。没有的类型不占位。对不上时 HTTP 200，`items` 为 `{}`，`card_links` 为 `[]`。条目带赞踩、采纳字段和 `comments`。员工待办另带已挂上的 `tasks`。`can_manage` 仅在调用人是上传人或汇报链上级时为 true；公司级管理人员为 false。

写入只接受上传人或其汇报链上级。公司级管理人员写入 404。赞踩和采纳是条目上的一份状态，后写覆盖：

- `POST /crm/leader-meeting-reviews/items/{unique_id}/feedback`：经营事项和员工待办。
- `POST /crm/leader-meeting-reviews/items/decisions`：只处理员工待办。经营事项 `ok` 为 false。
- `POST /crm/leader-meeting-reviews/items/{unique_id}/tasks`：只挂员工待办。任务须已存在。`adopt=true` 时同时标为已采纳。
- `POST /crm/leader-meeting-reviews/items/{unique_id}/comments`：两类都能追加和回复。带已有 `id` 时只改当前用户自己那条的正文。

## 卡片

标题 `例会复盘｜{date}`。日期优先用请求里的 `report_date`，否则用抽取行上的业务日。

每类只展开置信度最高的一条。经营事项写经营对象、管理问题、变化和负责人动作。员工待办写姓名、动作和截止。出口：

`{REVIEW_REPORT_HOST}/v2/business/leader-meeting-review/{report_id}/extract?tab={section}&date={date}`

单报告另开：`{REVIEW_REPORT_HOST}/v2/business/leader-meeting-review/{report_id}?date={date}`。列表、单报告、抽取是三个链接，对照拜访复盘，不合成一个页面。

收件人用 `user_profile_repo.get_by_oauth_user_id`，与今日重点同一套 open_id / platform。飞书走 markdown 卡片，两段之间有分隔线。钉钉走 markdown。成功 `pushed=true`。空抽取、没有 open_id、发送失败分别为 `empty_extracts`、`no_open_id`、`send_failed`，HTTP 200 且 `pushed=false`。

## 今日重点按钮

地址在 `push_page_urls.py`。待完成沿用任务列表，已拜访沿用拜访列表。例会复盘列表只带起止日期，两端都包含：

`{REVIEW_REPORT_HOST}/v2/business/leader-meeting-review?start_date={start}&end_date={end}`

今日重点按钮目前填入本周一到本周日。周一不放这个按钮。页面本身接受任意起止日期。

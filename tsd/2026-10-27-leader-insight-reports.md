# 技术规格说明书：负责人今日重点报告入口

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-27 |
| 变更窗口 | 2026-10-27 |
| 前置说明书 | `tsd/2026-10-23-comment-edit.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |

本文件描述负责人今日重点报告入口的 backend 实现。用户可见行为见 `fsd/2026-10-27-leader-insight-reports.md`。前端接入见 `backend/docs/workbench-today-highlights-frontend.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 报告槽位、筛选、列表正文、卡片蓝链 | `backend/app/services/leader_insight_reports.py` |
| 发卡、列表拼装、只有报告时的说明句 | `backend/app/services/department_today_highlights.py` |
| 列表 HTTP | `backend/app/api/routes/crm/today_highlights/router.py` 的 `GET /crm/today-highlights/departments` |
| 今日重点页 URL | `backend/app/utils/push_page_urls.py` 的 `build_department_highlights_url` |
| 推送说明书 | `backend/docs/notification-scenes.md` |
| 单测 | `backend/tests/test_leader_insight_reports.py`、`backend/tests/test_department_today_highlights.py` |

需介入写入仍走 `leader_intervention_actions.py`，只认抽取行。报告 `unique_id` 是洞察 id，写入会 404。

## 2. 数据

报告只读 `crm_entity_insight`。模型仍是 `backend/app/models/crm_entity_insight.py`，本仓库不建表。

`LEADER_REPORT_SLOTS` 固定三个槽位：

| key | 标题 | entity_type | insight_type |
| --- | --- | --- | --- |
| `key_deal_review` | 重点商机 | `USER` | `KEY_DEAL_REVIEW` |
| `leader_efficiency_insight` | 团队人效 | `USER` | `LEADER_EFFICIENCY_INSIGHT` |
| `team_operating_trend` | 经营趋势 | `USER` | `TEAM_OPERATING_TREND` |

`entity_id` 是负责人 oauth id，与需介入抽取的 `subject_id` 相同。`period_key` 等于 `report_date.isoformat()`。写入方存执行日 YYYY-MM-DD；带 `mon` 等后缀的键匹配不上。

查询：`entity_id`、`insight_type in (...)`、`entity_type=USER`、`is_deleted=0`、未过期。按 `generated_at`、`id` 降序。每个槽位在内存里取 `period_key` 相等的第一条。`unique_id` 为空，或 `summary` 与 `detail_text` 都空时，该槽位不出现。查库失败返回空列表，不挡住需介入。

洞察 `title`、`evidence`、`severity`、`category` 不进列表条目。

## 3. 列表

`highlights_for_leader(session, user_id, report_date)`：

1. `leader_oauth_id` 取当前用户 oauth id。没有则 `items={}`、`card_links=[]`。
2. 需介入走原来的 `load_department_extract_rows(..., oauth_user_ids=[oauth_id])`。
3. `load_leader_insight_sections` 追加报告 section。
4. `department_highlights_payload` 按 section 产出 `items` 和 `card_links`。缺席的 key 不会出现。
5. 顶层补上 `report_date`、`subject_id`。没有 `departments`、`department_id`、`department_name`。
6. 仅当存在 `leader_intervene` 时挂评论和任务。

报告条目：

```json
{ "unique_id": "insight-1", "summary": "摘要", "detail_text": "全文" }
```

`card_links[].title` 用槽位标题。`item_types` 用洞察类型。`url` 为 `/v2/department-highlights?date={report_date}&tab={key}`，不带 `department_id`。

## 4. 推送

`handle_department_today_highlights` 每天都会把 `_visit_only_leader_buckets` 并入收件人，不再限于周一、周五且完全没有抽取。因此周二到周四只有报告的负责人也能收到卡。

收件人：

- 有需介入抽取的负责人，按 oauth `subject_id` 找在职 profile。opt-out 不带部门 id。
- 再与 `_visit_only_leader_buckets` 合并。已有抽取卡的人保留抽取卡；没有抽取的人用空 sections 占位，以便挂拜访对照或报告。visit-only 路径 opt-out 带部门 id。

每人一张卡。装卡后读取拜访对照和 `load_leader_insight_reports`。`has_content`、拜访对照、报告三者都空才 `empty_highlights`。有报告就会发。

`_parent_department`、`department_ids_in_scope` 仍留在文件里，这次发送和列表都不调用。

## 5. 卡片结构

`render_card_sections` 三块：需介入、拜访对照、报告蓝链。飞书首块是需介入（或只有报告时的说明句加蓝链）；其余进 `extra_sections`，块前加分隔线。

只有报告时，报告块以 `REPORTS_ONLY_LEAD`（「今天没有需介入事项，可以先看这些报告。」）开头。有需介入或拜访对照时不加这句。

报告蓝链由 `report_entry_lines` 拼成一行，槽位之间两个空格。没有 url 或 label 的槽位不出现。

## 6. 测试

- `test_leader_insight_reports.py`：槽位筛选、`period_key` 按执行日、空 insight_types 不出现、正文只含摘要和全文、空白正文不进列表、卡片顺序、只有报告时的说明句。
- `test_department_today_highlights.py`：列表按人返回、无抽取时仍有报告、周二只有报告也发卡、有人有抽取时只有报告的人仍能收到、飞书分块。

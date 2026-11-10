# 技术规格说明书：团队今日重点标题和蓝链加「团队」前缀

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `tsd/2026-11-06-today-highlights-monday-weekly-report.md` |
| 后续说明书 | `tsd/2026-11-09-today-highlights-separate-cards.md`；`tsd/2026-11-10-team-highlights-friday-visit-watch.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、crm/today_highlights |

本文件描述团队今日重点卡片文案常量与渲染。用户可见行为见 `fsd/2026-11-09-team-highlights-team-prefix.md`。没有新增表或迁移。个人与团队各自推卡见后续说明书；本文件不再依赖折进个人块。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 需介入标题、底部「团队本周」、本周按钮、日报周报 | `backend/app/services/department_today_highlights.py` 常量 |
| 飞书、钉钉需介入标题固定用常量 | 同文件 `_intervention_markdown`、`render_dingtalk_markdown` |
| 报告入口蓝链加前缀 | 同文件 `_team_link_label`、`_report_card_lines` |
| 拜访对照小节与查看链 | `backend/app/services/leader_visit_watch.py` 的 `_team_visit_section_label`、`_detail_link` |
| 单测 | `test_department_today_highlights.py`、`test_leader_insight_reports.py`、`test_leader_visit_watch.py` |

## 2. 行为

`LEADER_REPORT_SLOTS` 和页面组装的 `title` / `label` 不改。只有发卡渲染时给报告入口补「团队」前缀；已以「团队」开头的标签原样保留。拜访对照的 `label` 仍存「上周」/「本周」，渲染时再拼成「团队上周拜访」等。

一线单独卡继续用快照小节名「立即处理」。

「团队待完成」由 `_week_task_button` 调用 `build_task_list_page_url`，只传本周 `due_date__gte` / `due_date__lte` 与未完成 `status__in`，不传 `owner_name`。一线 `week_task_summary_link` 仍传本人姓名。

列表接口 `GET /crm/today-highlights/departments` 仍返回快照或槽位原标题。

# 技术规格说明书：今日重点恢复销售个人与团队各自推卡

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `tsd/2026-11-04-today-highlights-one-card.md`、`tsd/2026-11-09-team-highlights-team-prefix.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |

用户可见行为见 `fsd/2026-11-09-today-highlights-separate-cards.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 合并任务两边各自调用 | `backend/app/tasks/cron_jobs.py` 的 `push_crm_today_highlights` |
| 销售推送不再排除负责人 | `backend/app/services/sales_today_highlights.py` 的 `handle_sales_today_highlights` |
| 团队推送不再折进个人块 | `backend/app/services/department_today_highlights.py` 的 `handle_department_today_highlights` |
| 单测 | `test_sales_today_highlights.py`、`test_department_today_highlights.py` |

## 2. 行为

删除 `exclude_oauth_user_ids` 与 `covered_by_team_card`。删除 `personal_highlights_for_leader` 及向 `card["personal_highlights"]` 的写入。

`sent_oauth_user_ids` 仍可出现在团队推送结果里，供排查；定时任务不再用它过滤一线卡。

卡片渲染仍兼容偶发的 `personal_highlights` 字段，但生产路径不再写入。

同一行多条 markdown 蓝链的分隔符是常量 `CARD_LINK_SEPARATOR = " ｜ "`，落在 `sales_today_highlights.render_card_links` / `render_action_buttons`、`leader_insight_reports.report_entry_lines`，以及团队卡拼日报/周报与钉钉底部蓝链处。

需介入与拜访对照同时有时：`_team_parts` 拆成两段；`render_card_sections` 分块发给飞书；`_team_markdown` / 钉钉渲染在两段之间插入 `---`。

周五对照段 `include_detail=False` 的落点见 `tsd/2026-11-10-team-highlights-friday-visit-watch.md`。

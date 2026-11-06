# 技术规格说明书：周一今日重点放上周周报

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-06 |
| 变更窗口 | 2026-11-06 |
| 前置说明书 | `tsd/2026-11-04-today-highlights-monday-omits-daily-report.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |

本文件描述周一团队卡放上周周报的实现。用户可见行为见 `fsd/2026-11-06-today-highlights-monday-weekly-report.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 周一放周报、其余日子放日报 | `backend/app/services/department_today_highlights.py` 的 `_team_report_line` |
| 上一 ISO 周 | 同文件的 `_previous_iso_week` |
| 周报 URL | `backend/app/utils/push_page_urls.py` 的 `build_team_weekly_report_url` |
| 单测 | `backend/tests/test_department_today_highlights.py`、`backend/tests/test_push_page_urls.py` |

## 2. 日期

`report_date.weekday() == 0` 时，用 `report_date - 7 days` 的 `isocalendar()` 取 ISO 周年和周序，调用 `build_team_weekly_report_url`。路径是 `/v2/business/reports?tab=weekly-team&year=&week=`。周序不补零。其余日子仍用 `report_date - 1 day` 调用 `build_team_daily_report_url`。host 为空时不放。它跟报告入口写在同一行，不是 `card_footer_buttons` 的一项，也不单独触发发卡。

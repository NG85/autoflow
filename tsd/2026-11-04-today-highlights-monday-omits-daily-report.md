# 技术规格说明书：周一今日重点不放日报

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-04 |
| 变更窗口 | 2026-11-04 |
| 前置说明书 | `tsd/2026-11-04-today-highlights-one-card.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |

本文件描述周一团队卡不放日报的实现。用户可见行为见 `fsd/2026-11-04-today-highlights-monday-omits-daily-report.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 按星期决定是否放日报 | `backend/app/services/department_today_highlights.py` 的 `_team_report_line` |
| 日报 URL | `backend/app/utils/push_page_urls.py` 的 `build_team_daily_report_url` |
| 单测 | `backend/tests/test_department_today_highlights.py` |

## 2. 日期

`report_date.weekday() == 0` 时 `_team_report_line` 返回空串，不占块。其余日子用 `report_date - 1 day` 调用 `build_team_daily_report_url`。路径是 `/v2/business/reports?tab=daily-team&date=`。host 为空时同样不放。它仍不是 `card_footer_buttons` 的一项。

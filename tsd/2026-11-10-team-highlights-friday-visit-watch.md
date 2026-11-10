# 技术规格说明书：周五团队拜访对照不放正文查看链

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-10 |
| 变更窗口 | 2026-11-10 |
| 前置说明书 | `tsd/2026-11-09-team-highlights-team-prefix.md`、`tsd/2026-11-09-today-highlights-separate-cards.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、crm/today_highlights |

用户可见行为见 `fsd/2026-11-10-team-highlights-friday-visit-watch.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 周五关闭对照段明细链 | `department_today_highlights._watch_section`：`include_detail = weekday != 4` |
| 明细链文案 | `leader_visit_watch.visit_watch_lines` / `_detail_link` |
| 底部已拜访 | `department_today_highlights._week_visit_button`（周一不放） |
| 单测 | `test_leader_visit_watch.py`、`test_department_today_highlights.py` |

## 2. 行为

`visit_watch` 仍带 `url`。`_detail_link` 蓝链文案直接用小节名（如「团队上周拜访」），不加「查看」。周五渲染对照段时不输出该蓝链；底部「团队已拜访」用同一周起止与部门名打开拜访列表。

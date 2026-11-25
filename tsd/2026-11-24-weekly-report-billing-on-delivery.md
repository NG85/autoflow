# 技术规格说明书：周报只在卡片真正发出后计费

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-24 |
| 变更窗口 | 2026-11-24 |
| 前置说明书 | `tsd/2026-11-24-daily-report-billing-on-delivery.md` |
| 对应 FSD | `fsd/2026-11-24-weekly-report-billing-on-delivery.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/weekly_report、billing |

用户可见行为见对应 FSD。没有新表，没有迁移，没有新接口。

## 1. 代码落点

`generate_crm_weekly_report` 里，部门周报和公司周报原来在 `result["success"]` 且 `weekly_report_has_data` 时调用 `_report_task_usage_once`。统计卡关闭时 `skip_kpi_card_result` 返回 `success=True`、`success_count=0`，因此未发卡也会上报 `CRM_TEAM_WEEKLY_REPORT`。

现在改为 `weekly_report_should_bill`：先过 `_daily_push_delivered`，再要求 `weekly_report_has_data`。用量键仍是 `weekly-department:{end_date}:{部门名}` 和 `weekly-company:{end_date}`。

单测：`backend/tests/test_weekly_report_billing_on_delivery.py`。

## 2. 不改的路径

推送成功计数和失败告警仍按原来的 `success` 判断。周跟进总结按生成结果上报，不看周报卡片。拜访报告 Markdown 变体没有这条计费。

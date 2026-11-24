# 技术规格说明书：日报只在真正发出后计费

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-24 |
| 变更窗口 | 2026-11-24 |
| 前置说明书 | `tsd/2026-10-27-today-highlights-billing.md` |
| 对应 FSD | `fsd/2026-11-24-daily-report-billing-on-delivery.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/daily_report、billing |

用户可见行为见对应 FSD。没有新表，没有迁移，没有新接口。

## 1. 代码落点

计费判断在 `backend/app/services/crm_statistics_service.py`。

`skip_kpi_card_result` 在统计卡关闭时返回 `success=True`、`skipped=True`、`success_count=0`。三处日报原来只看 `success`，因此会在未发卡时调用 `report_billing_usage`。

现在先用 `_daily_push_delivered`：`skipped` 为真、`success` 不为真、或 `success_count` 不大于 0，都不算发出。发出之后，个人日报直接上报 `CRM_SALES_PERSONAL_DAILY`；团队日报、公司日报仍只在 `daily_report_has_follow_up` 为真时上报 `CRM_SALES_TEAM_DEPARTMENT_DAILY`、`CRM_SALES_TEAM_COMPANY_DAILY`。用量键不变。

单测：`backend/tests/test_daily_report_billing_on_delivery.py`。

## 2. 不改的路径

今日重点仍在发卡成功后按人上报。周报计费见 `tsd/2026-11-24-weekly-report-billing-on-delivery.md`。周跟进总结、拜访保存、复盘对话的计费不改。补发单个部门日报的接口本身不上报。

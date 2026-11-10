# 技术规格说明书：立即处理一对一绑定任务

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-10 |
| 变更窗口 | 2026-11-10 |
| 前置说明书 | `tsd/2026-11-10-sales-highlights-key-findings.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/today_highlights |

用户可见行为见 `fsd/2026-11-10-sales-immediate-status-null.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 一条任务、读出时清空 status | `sales_today_highlights._bound_task_id`、`_include_row`、`_serialize_item` |
| 不再按主张合并 | `assemble_sales_highlights` 只按抽取键折叠 |
| 拒绝写入立即处理的决定 | `sales_immediate_actions.save_sales_highlight_decisions` |
| 单测 | `test_sales_today_highlights.py`、`test_sales_immediate_actions.py` |

## 2. 规则

`IMMEDIATE_ACTION` 没有任务编号时不进入组装结果。有编号时 `related_todos` 只有这一条，返回里的 `adopted_todo_id` 也写成这个编号，`status` 为 `null`。不写回抽取行。

决定接口先认出立即处理，这条 `ok` 为 false，不核对它携带的任务编号，也不改该行。同批里的关键发现、下周待确认仍按原规则写入；这些条目带了不存在的任务编号时，整批 422。

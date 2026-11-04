# 技术规格说明书：今日重点计费

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-27 |
| 变更窗口 | 2026-10-27 |
| 前置说明书 | `tsd/2026-10-27-leader-insight-reports.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/today_highlights、billing |

本文件描述今日重点计费的 backend 实现。用户可见行为见 `fsd/2026-10-27-today-highlights-billing.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 场景枚举与 SKU 映射 | `backend/app/services/feishu_billing_facade.py` |
| 额度查询、用量键、页面链接 | `backend/app/services/today_highlights_billing.py` |
| 销售发卡前后调用 | `handle_sales_today_highlights` |
| 负责人发卡前后调用 | `handle_department_today_highlights` |
| 单测 | `backend/tests/test_today_highlights_billing.py`、`backend/tests/test_feishu_billing_scenario_enabled.py` |

## 2. 场景

| 场景 | `ai_module_key` | 对应日报 |
| --- | --- | --- |
| `CRM_SALES_TODAY_HIGHLIGHTS` | `sales_personal_daily_report` | 销售个人日报 |
| `CRM_DEPARTMENT_TODAY_HIGHLIGHTS` | `sales_team_daily_report` | 部门团队日报 |

同一 SKU 共用开通状态。关掉个人日报 SKU 会同时关掉销售今日重点；关掉团队日报 SKU 会同时关掉负责人今日重点。

用量键（unique_key，不超过飞书 `trace_id` 64 字符）：

- 销售：`sales-today:{report_date}:{user_id}`
- 负责人：`dept-today:{report_date}:{user_id}`

`user_id` 过长时改为 `h` + `sha256(user_id)[:32]`，与原文 id 分区，避免和短 id 撞键。实际上报的 `trace_id` 是 `{ai_module_key}-{sha256[:24]}`，同样不超过 64。确定性哈希会混入租户 id。`review_detail` 为今日重点页 URL，拼不出时回退 `REVIEW_REPORT_HOST`。

## 3. 调用顺序

变体关闭 → `billing_blocked`（SKU 关闭或额度不足）→ 读抽取并发卡。成功发送后再上报。上报异常只记日志，不把已发出的卡改成失败。

`CRM_BILLING_ENABLED` 为假时查询和上报都是空操作，发卡不受影响。

## 4. 测试

- 销售、负责人分别走到对应日报场景。
- 用量键不与 `sales-daily:` / `department-daily:` 相同，且不超过 64 字符。
- 额度不足时不读抽取、不发卡。
- 发送失败不上报。
- 共享 SKU 状态覆盖今日重点场景。

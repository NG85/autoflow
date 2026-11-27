# 技术规格说明书：拜访复盘经营复盘优先统一报告

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-27 |
| 变更窗口 | 2026-11-27 |
| 前置说明书 | `tsd/2026-11-12-visit-recap-audience.md`、`tsd/2026-11-09-visit-recap-performance.md` |
| 关联服务 | backend；Aldebaran |
| 关联功能模块 | crm/visit_records、notification |

本文件描述经营复盘的读取顺序。用户可见行为见 `fsd/2026-11-27-visit-recap-unified.md`。没有新增表或迁移。卡片发送函数不改，它消费挑选后的槽位。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 类型常量和挑选 | `backend/app/services/visit_record_insight_reader.py` 的 `pick_business_insight`、`load_visit_record_insights_by_view`、`select_one_business_recap_per_visit` |
| 复盘页 | `resolve_viewer_recap` 仍读销售、上级两个槽位 |
| 列表 `has_recap` | `viewer_has_visible_recap`；`visit_record.py` 的 `_fill_has_recap` |
| 轻量卡 | `platform_notification_service.send_visit_record_notification` 调用 `load_visit_record_insights_by_view` |
| 本周拜访 | `sales_week_visits.py` |
| 团队拜访对照 | `leader_visit_watch.py` |
| 抽取 | `visit_record_extract_reader.py`，查询条件不变 |
| 单测 | `backend/tests/test_visit_record_insight_reader.py` |

## 2. 洞察

仍读 `crm_entity_insight`，`entity_type=VISIT`，未删除且未过期。经营复盘类型：

| 常量 | `insight_type` | 用途 |
| --- | --- | --- |
| `VISIT_INSIGHT_UNIFIED_TYPE` | `POSTVISIT_REVIEW` | 现行统一报告 |
| `VISIT_INSIGHT_SALES_TYPE` | `POSTVISIT_REVIEW_SALES_VIEW` | 没有统一报告时的销售侧历史报告 |
| `VISIT_INSIGHT_LEADER_TYPE` | `POSTVISIT_REVIEW_LEAD_VIEW` | 没有统一报告时的上级侧历史报告 |

`pick_business_insight` 先在 `preferred_types=(POSTVISIT_REVIEW,)` 里取最新一条。没有再按 `fallback_type` 取最新一条。同类型的新旧由既有 `pick_visit_record_insight` 按生成时间决定。

`load_visit_record_insights_by_view` 的两个经营复盘槽位都走这个函数：

| 槽位 | fallback |
| --- | --- |
| `sales` | `POSTVISIT_REVIEW_SALES_VIEW` |
| `leader` | `POSTVISIT_REVIEW_LEAD_VIEW` |

有统一报告时两个槽位是同一行。`performance_sales`、`performance_leader` 仍只取各自的 `VISIT_SALES_PERFORMANCE_*`。

`load_visit_record_insight` 在调用方传入 `preferred_types` 时按传入类型挑选。未传时，`view=sales/leader` 走 `pick_business_insight`，fallback 为该视角的历史类型；不传 `view` 时 fallback 为历史销售视角。

`_VISIT_PAGE_INSIGHT_TYPES` 含 `POSTVISIT_REVIEW`，批量类型查询能看见统一报告。

## 3. 卡片和复盘页

`send_visit_record_notification` 在 `uses_recap_lite()` 时取四个槽位。销售卡和记录人卡用 `sales` 槽位加销售表现评价。上级卡用 `leader` 槽位。`leader_personal` 在上级表现评价摘要非空时，用同一个 `leader` 槽位加上级表现评价。抽取链接仍在 `has_visit_recap_insight` 为真时读取，只挂记录人卡。

`resolve_viewer_recap` 的身份顺序不变。记录人和协同人读 `sales` 槽位，抄送、汇报上级和其他能看拜访的人读 `leader` 槽位。因此有统一报告时，两边的 `insight` 是同一份。

## 4. 列表

`viewer_has_visible_recap` 在类型集合里有 `POSTVISIT_REVIEW` 时返回 true。没有统一报告时保持原有销售视角、上级视角和人员表现评价判断。

`_fill_has_recap` 把统一报告和历史上级业务推进都视为已有业务推进。这时不再为了「只有上级人员表现评价」去查公司级角色和汇报链。

## 5. 统计

`SALES_BUSINESS_RECAP_TYPES` 是 `POSTVISIT_REVIEW` 与 `POSTVISIT_REVIEW_SALES_VIEW`。本周拜访和团队拜访对照的查询使用这个集合。`select_one_business_recap_per_visit` 按 `entity_id` 分组：组内有统一报告就留最新一条统一报告，否则留最新一条 fallback（默认历史销售视角）。`POSTVISIT_REVIEW_LEAD_VIEW` 不进入这组统计。

## 6. 抽取

`load_visit_record_extract_response` 默认 `viewer_role=sales`，条件仍是 `visit_id`、未删除、`status != SUPERSEDED`。不按 `insight_type` 过滤。Aldebaran 把统一报告和历史销售视角的抽取都写成 `viewer_role=sales`，历史负责人视角不抽。条目类型、`card_links` 和 `profile_id` 不变。

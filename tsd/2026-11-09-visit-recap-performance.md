# 技术规格说明书：拜访复盘增加人员表现评价

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `tsd/2026-10-19-release.md` |
| 关联服务 | backend；Aldebaran；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/visit_records、notification |

本文件描述人员表现评价的读取、复盘页返回和轻量卡正文。用户可见行为见 `fsd/2026-11-09-visit-recap-performance.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 洞察类型和按视角挑选 | `backend/app/services/visit_record_insight_reader.py` |
| 复盘页 `insight`、`performance`、`tabs` | `backend/app/services/visit_record_recap.py` |
| 列表 `has_recap` | `viewer_has_visible_recap`；批量类型查询含表现评价 |
| 轻量卡正文 | `backend/app/services/visit_record_recap_card.py` 的 `performance_summary_text`、`compose_recap_card_summary` |
| 发给谁哪一张 | `backend/app/services/platform_notification_service.py` 的 `send_visit_record_notification`、`_recap_card_for_role` |
| 单测 | `backend/tests/test_visit_record_recap.py`、`test_visit_record_insight_reader.py`、`test_visit_record_recap_card.py`、`test_visit_record_mixed_card_send.py` |

## 2. 洞察

仍读 `crm_entity_insight`，`entity_type=VISIT`。一次查询带回四类，各取最新一条未删除、未过期的：

| 视角 | 业务推进 | 人员表现评价 |
| --- | --- | --- |
| 销售 | `POSTVISIT_REVIEW_SALES_VIEW` | `VISIT_SALES_PERFORMANCE_SALES_VIEW` |
| 上级 | `POSTVISIT_REVIEW_LEAD_VIEW` | `VISIT_SALES_PERFORMANCE_LEADER_VIEW` |

`resolve_visit_record_page` 在四类都没有，或当前用户没有可看视角时返回空。有任一可看报告时返回 `(视角, 该视角业务推进, 该视角表现评价)`，缺的一项是 `None`。

报告对外字段不变：`unique_id`、`insight_type`、`title`、`summary`、`detail_text`、`category`、`severity`、`generated_at`。

## 3. 卡片

`build_recap_lite_card` 的表现评价只读 `summary`，经 `_display_text` 丢掉空值和占位符，再截断到 800 字。

发送时：

- 记录人卡、销售视角卡带销售表现评价。有业务推进时，抽取入口仍只挂在记录人卡上。
- `leader_personal` 仅在上级表现评价摘要非空时生成，只发给 `type=leader` 的个人。
- 抄送以及 `is_group=true` 的 review、brief 群用上级业务推进卡，不带表现评价。

没有业务推进时不读抽取表，因此只有表现评价也不会给卡片加抽取按钮。复盘页对记录人仍读抽取。

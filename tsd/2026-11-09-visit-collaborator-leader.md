# 技术规格说明书：协同人与 leader 重复时取 leader

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `tsd/2026-11-09-visit-review-group-replaces.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、visit_record |

本文件描述收件人合并顺序。用户可见行为见 `fsd/2026-11-09-visit-collaborator-leader.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 先放记录人和 leader，再跳过已有 `open_id` 的协同人 | `backend/app/services/platform_notification_service.py` 的 `_collect_visit_record_recipients_and_groups` |
| 同一 `open_id` 只发一张，优先级 recorder、leader、configured_cc、collaborative_participant | 同文件 `_send_visit_record_to_individual_recipients` |
| 单测 | `backend/tests/test_visit_record_cc_collect.py`、`backend/tests/test_visit_record_card_recipient_dedup.py` |

## 2. 顺序

`get_recipients_for_recorder` 先写入记录人和汇报上级。协同人按平台并入时，`open_id` 已经在该平台列表里就不再追加。

因此同一个人不会同时留下 `leader` 和 `collaborative_participant`。后面的 `groups.review.replaces` 只看到 leader。群接替 leader 时，这个人不会因为协同人身份被留下。

抄送并入时同样跳过已有 `open_id`。发送阶段再用角色优先级兜底，数值小的先发。

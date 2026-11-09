# 技术规格说明书：拜访群可以不接替 leader

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `tsd/2026-10-10-autoflow-baseline.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、visit_record |

本文件描述 `groups.review.replaces` 的解析和收件人过滤。用户可见行为见 `fsd/2026-11-09-visit-review-group-replaces.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 解析 `replaces`，缺省 `leader` 与 `configured_cc` | `backend/app/services/visit_record_push_policy.py` 的 `review_replaces` |
| 有群且群开着时按名单拿掉个人 | `backend/app/services/platform_notification_service.py` 的 `_collect_visit_record_recipients_and_groups`。协同人并入发生在这之前，同一 `open_id` 已是 leader 则不再追加 |
| 预览走同一收集函数 | `backend/app/services/notification_preview.py` |
| 单测 | `backend/tests/test_visit_record_push_policy.py`、`backend/tests/test_visit_record_cc_collect.py` |

## 2. 行为

`replaces` 只认 `leader` 和 `configured_cc`。不认识的名字丢掉。字段不是数组时视为没写。`configured_cc` 被接替时仍保留 `cc_scope=user`。

`default_settings.yml` 不写这个字段，缺省即当前接替行为。

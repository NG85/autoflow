# 技术规格说明书：本次拜访商机评估推送

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-20 |
| 变更窗口 | 2026-11-20 |
| 关联服务 | backend；Aldebaran；飞书 / Lark / 钉钉 / 企微 |
| 关联功能模块 | notification |

用户可见行为见 `fsd/2026-11-20-visit-opportunity-review-push.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 请求体 | `backend/app/api/routes/notification_schemas.py` 的 `VisitOpportunityReviewPushRequest` |
| 路由分发 | `backend/app/api/routes/notification.py` 的 `_handle_visit_opportunity_review_push`，挂在 `POST /notification/push` |
| 发送 | `backend/app/services/visit_opportunity_review_push.py` |
| 开关 | `backend/app/core/config.py` 的 `VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED`，默认 `false` |
| 接收人 | `PlatformNotificationService._collect_visit_record_recipients_and_groups`，再经 `filter_recipients_by_policy` |
| 部门群 | `report_markdown_dispatch._send_markdown_to_groups`。飞书、钉钉、企微同一份群名单；卡片按平台组装 |
| 单测 | `backend/tests/test_visit_opportunity_review_push.py` |

## 2. 请求

判别字段 `type=visit_opportunity_review`。联合类型里放在 `visit_record_card` 之前。

| 字段 | 约束 |
| --- | --- |
| `record_id` | 必填。拜访记录 `crm_sales_visit_records.record_id` |
| `content` | 必填。评估 Markdown |
| `title` | 可选。飞书卡片标题；缺省时由卡片构造取正文首行 |
| `opportunity_id` | 可选。原样回传 |
| `report_id` | 可选。原样回传 |
| `content_type` | 固定 `markdown` |
| `delivery` | `card`（默认）或 `post`。只影响飞书 / Lark |

空 `record_id` 或空正文返回 422。开关关闭时也先做这两项校验。

## 3. 开关与查找

`VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED` 为 false 时，校验通过后立即返回：

```json
{
  "success": true,
  "skipped": true,
  "reason": "feature_disabled",
  "type": "visit_opportunity_review",
  "message": "visit opportunity review push is disabled",
  "recipients_count": 0,
  "groups_count": 0,
  "success_count": 0,
  "failed_recipients": []
}
```

外层仍是 `{ "code": 200, "message": "ok", "result": ... }`。

开关打开后，按 `record_id` 读 `CRMSalesVisitRecord`。没有行返回 404。行转成拜访卡片使用的 push dict，并 `fill_sales_visit_record_fields`。记录人 id 和姓名都空时，HTTP 200，`success=false`，`message=No recorder found`。

## 4. 接收人

调用 `_collect_visit_record_recipients_and_groups`，传入记录人姓名、记录人 id、拜访 dict 和 `load_visit_record_push_policy()` 的结果。然后 `filter_recipients_by_policy`。

群只纳入策略里 `group_enabled("review")` 的 review 群和 `group_enabled("brief")` 的 brief 群。按 `(platform, chat_id)` 去重。

个人和群都空时，HTTP 200，`success=false`，`message=No recipients found`。

## 5. 投递

个人按平台发送：

| 平台 | `msg_type` | 载荷 |
| --- | --- | --- |
| 飞书 / Lark，`delivery=card` | `interactive` | `build_feishu_markdown_card` |
| 飞书 / Lark，`delivery=post` | `post` | `build_feishu_post_markdown` |
| 企微 | `template_card` | `build_link_notice_from_markdown`，`task_prefix=vor`。未配置 Sia 时该收件人失败 |
| 钉钉 | `text` | Markdown 原文 |

部门群与个人使用同一份正文，进入同一个 `_send_markdown_to_groups`。群名单不按渠道拆开。各平台只负责自己的卡片：

| 平台 | 群 `msg_type` | 载荷 |
| --- | --- | --- |
| 飞书 / Lark，`delivery=card` | `interactive` | `build_feishu_markdown_card` |
| 飞书 / Lark，`delivery=post` | `post` | `build_feishu_post_markdown` |
| 企微 | `template_card` | `build_link_notice_from_markdown` |
| 钉钉 | `text` | Markdown 原文 |

未配置企微 Sia 时，`_send_content_to_group_chats` 跳过该平台的群，其余平台照发。

单人失败写入 `failed_recipients`，继续其余收件人。`success` 为 `success_count > 0`。`success_count` 含个人成功数与群成功数。`recipients_count` 只计个人。

本路径不调用拜访卡片推送状态更新，不走计费上报，不挂 `OPS_CC_*` / `DEMO_CC_*`。

## 6. 环境

`.env.example` 增加 `VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED=false`。未配置时默认关闭。

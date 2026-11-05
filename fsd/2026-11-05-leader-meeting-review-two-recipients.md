# 功能规格说明书：例会复盘卡片发给负责人和提交人

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-05 |
| 变更窗口 | 2026-11-05 |
| 前置说明书 | `fsd/2026-10-29-leader-meeting-review-card.md` |
| 关联服务 | backend；Aldebaran；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、crm/leader_meeting_review |

本文件只改发卡对象。卡片内容、查看权限和页面链接仍见前置说明书。历史说明书不改。

## 1. 这次做什么

Aldebaran 抽取成功后调用 `POST /api/v1/notification/push`，`type=leader_meeting_review_card`。`owner_id` 是这次例会复盘的业务负责人，抽取行挂在这个人身上。`creator_id` 是 Aldebaran 从 `bi_reports.created_by` 取出的提交人。两人都收一张同样的卡。

## 2. 发给谁

| 请求 | 发卡 |
| --- | --- |
| 只有 `owner_id` | 负责人一张 |
| `creator_id` 与 `owner_id` 相同 | 一张 |
| 两个 id 不同 | 各一张，内容相同 |

不进群，不计费。其中一人没有 open_id 或发送失败时，另一人仍收卡。两人都发不出时不发卡片，接口仍是 HTTP 200。没有抽取时也不发。再次调用会再发。

提交人能打开卡片查看抽取，也可以改：赞踩、采纳、拒绝、评论和挂任务与负责人相同。负责人汇报链上两级内的上级仍可改。其他人只看。

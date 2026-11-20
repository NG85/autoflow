# 功能规格说明书：本次拜访商机评估推送

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-20 |
| 变更窗口 | 2026-11-20 |
| 关联服务 | backend；Aldebaran 拜访商机评估；飞书 / Lark / 钉钉 / 企微 |
| 关联功能模块 | notification |

Aldebaran 负责对本次拜访关联商机做评估，并调用本服务推送。评估何时跑、正文如何落库，见 Aldebaran `fsd/agent/fsd_visit_opportunity_review.md`。本文件只写推送。

## 1. 这次做什么

`POST /api/v1/notification/push` 增加 `type=visit_opportunity_review`。调用方传入拜访 `record_id` 和评估 Markdown。本服务按拜访卡片的同一套名单解析接收人，再把 Markdown 发给这些人，以及会收拜访卡片的部门群。

调用方不传接收人名单。

## 2. 开关

`VISIT_OPPORTUNITY_REVIEW_PUSH_ENABLED`，默认关闭。

关闭时接口成功返回，`result.skipped=true`，`reason=feature_disabled`。不查拜访记录，不解析接收人，不发消息。

打开后才按下面的规则发送。Aldebaran 侧还有独立开关，决定是否调用评估技能。两边都打开，接收人才会收到这条消息。

## 3. 发给谁

与 `visit_record_card` 使用同一套解析和 `visit_record_push_policy`。飞书、钉钉、企微的部门群走这一套，不按渠道另选人：

- 记录人、汇报上级、协同人、抄送。
- 会收拜访卡片的部门群（review 群、brief 群，按策略开关）。
- 群接替个人角色的规则与拜访卡片相同。

没有记录人时不发。个人和群都没有可发对象时不发。

这条推送不改拜访卡片的推送状态，也不重复计费。不走运维抄送，不走演示旁路。

## 4. 收到什么

正文是调用方传入的 Markdown。标题用请求里的 `title`。个人和部门群收到同一份正文，卡片形态按平台各自组装。

| 平台 | 形态 |
| --- | --- |
| 飞书 / Lark | 默认交互卡片。`delivery=post` 时发富文本 |
| 企微 | 模板卡片 |
| 钉钉 | 文本 |

## 5. 验收

- 开关默认关闭。关闭时返回 `skipped`，不查库、不发消息。
- 打开后，个人接收人与部门群与同一条拜访的拜访卡片一致。
- 请求不接受 `recipient_user_ids`。
- 拜访记录不存在返回 404。`record_id` 或正文为空返回 422。
- 发送失败记在返回的失败名单里，不改拜访卡片推送状态。

# 功能规格说明书：协同人与 leader 重复时取 leader

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `fsd/2026-11-09-visit-review-group-replaces.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、visit_record |
| 前端接入 | `backend/docs/visit-record-push-policy.md` |

本文件固定同一个人的收件角色。卡片种类和群是否接替仍按前置说明书。历史说明书不改。

## 1. 这次做什么

同一个人既是这条拜访的汇报上级，又是协同人时，只按 leader 收卡。

因此他收到的是 leader 的卡片，不是协同人的销售卡。有拜访群并且群接替 leader 时，这个人被接替，不会改以协同人再收一张个人卡。

纯协同人，以及只是记录人的人，不受影响。同一个人已是记录人时，仍按记录人，优先级高于 leader。

## 2. 验收

- 没有拜访群时，这个人的角色是 leader，列表里没有第二条协同人。
- 群接替 leader 时，这个人不在个人收件人里；其他纯协同人仍收个人卡。
- `replaces: []` 时，这个人按 leader 个人收，卡片种类用 `recipients.leader.card`。
- 发送时若同一 `open_id` 仍出现多次，只发优先级更高的一张：记录人、leader、抄送、协同人。

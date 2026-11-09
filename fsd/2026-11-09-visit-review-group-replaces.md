# 功能规格说明书：拜访群可以不接替 leader

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `fsd/2026-10-10-autoflow-baseline.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、visit_record |
| 前端接入 | `backend/docs/visit-record-push-policy.md` |

本文件只增加拜访群接替哪些个人收件人的配置。卡片种类仍按角色单独配。历史说明书不改。

## 1. 这次做什么

`groups.review.replaces` 决定有拜访群时，哪些个人不再单独收、改由群收。

不写这个字段时，仍接替 leader，以及公司级、部门级抄送。某家客户不想接替时，只给这家写 `replaces: []`：群收自己的卡，leader 按 `recipients.leader.card` 另外收。

记录人、纯协同人、按具体销售配置的抄送始终单独收。同一个人既是协同人又是汇报上级时，先记成 leader，见 `fsd/2026-11-09-visit-collaborator-leader.md`。群关掉时不接替任何人。

## 2. 验收

- 不写 `replaces` 的客户，有拜访群时 leader 个人不收。
- `replaces: []` 时，群和个人都收，卡片种类各自按 `card`。
- `replaces: ["leader"]` 时，只拿掉 leader，公司级抄送仍发个人。
- 群 `enabled: false` 时，即使配了群，也不拿掉个人。

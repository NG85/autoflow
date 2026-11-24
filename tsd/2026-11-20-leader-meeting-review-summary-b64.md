# 技术规格说明书：例会复盘卡片解码会议总结

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-20 |
| 变更窗口 | 2026-11-20 |
| 前置说明书 | `tsd/2026-11-06-leader-meeting-review-todo-candidates.md` |
| 对应 FSD | `fsd/2026-11-20-leader-meeting-review-summary-b64.md` |
| 关联服务 | backend |
| 关联功能模块 | notification、crm/leader_meeting_review |

用户可见行为见对应 FSD。没有新增表、接口或迁移。查询仍是 `bi_reports` 按 `report_id` 取最新一行的 `report_content`。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 识别前缀并解码 | `backend/app/services/leader_meeting_review_card.py` 的 `decode_stored_summary` |
| 读总结时调用 | 同文件 `summary_from_report_content`，`load_report_summary` 不另写分支 |
| 飞书 / 钉钉 / 企微正文 | 仍读组装结果里的 `summary`，不在各端再解码 |
| 单测 | `backend/tests/test_leader_meeting_review_card_push.py` |

## 2. 解码

`summary` 去掉首尾空白后，不以 `__b64__:` 开头就原样返回。

以前缀开头时，去掉前缀和其中空白，按标准 base64 解码，再按 UTF-8 得到文本。缺的 `=` 补上。解码失败、前缀后为空，或结果去掉空白后为空，返回空字符串，并打一条不含原文的警告。空字符串沿用现有空总结：有抽取时正文是 `--`，否则 `empty_extracts` 不发卡片。

## 3. 不改的路径

`load_report_summary` 的 SQL、抽取查询、收件人、报告链接和按钮都不改。报告页仍由 base-api 提供，这次不解码页面上的总结。

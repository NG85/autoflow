# 技术规格说明书：例会复盘卡片展示会议总结

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-06 |
| 变更窗口 | 2026-11-06 |
| 前置说明书 | `tsd/2026-11-05-leader-meeting-review-two-recipients.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、crm/leader_meeting_review |

本文件描述抽取查询和发卡文案的调整。用户可见行为见 `fsd/2026-11-06-leader-meeting-review-todo-candidates.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 会议总结 | `backend/app/services/leader_meeting_review_card.py` 的 `load_report_summary`，读 `bi_reports.report_content` |
| 页面返回两类，卡片按钮也用这两类 | 同文件 `_report_extract_statement`、`SECTIONS`、`extract_action_buttons` |
| 卡片蓝链和飞书按钮 | 同文件 `feishu_card_parts`、`render_dingtalk_markdown`，调用 `build_leader_meeting_review_report_url` |
| 报告 URL | `backend/app/utils/push_page_urls.py` 的 `build_leader_meeting_review_report_url`，不传日期 |
| 单测 | `backend/tests/test_leader_meeting_review_card_push.py` |

## 2. 查询

两类查询共用 `_report_extract_statement`，按传入的 `item_type` 收窄。其余条件不变：`source_report_unique_id`、`subject_type=USER`、未删除、`status` 不是 `SUPERSEDED`。

页面 `load_report_extract_rows` 读 `EMPLOYEE_TODO_CANDIDATE` 和 `MANAGEMENT_EVENT`。`SECTIONS` 的顺序是待办候选、经营事项。没有的一类不放进 `items` 和 `card_links`。

发卡 `load_extract_rows` 读这两类，并再加 `subject_id=owner_id`。`load_report_summary` 取 `report_id` 最新一行，解析 JSON 的 `summary`。读失败或没有该字段时总结为空。没有总结、也没有抽取时，发卡按 `empty_extracts` 跳过。

写入接口仍按 `unique_id` 查找，本文件不改赞踩、采纳和评论。

## 3. 卡片

飞书沿用拜访轻量卡的组件：正文是总结，没有总结时写 `--`；`footer` 是 `[完整复盘报告](url)`，字号 normal；`footer_buttons` 是抽取按钮。按钮文案用 `card_links[].title`，条数大于 1 时写成「标题（N）」。`url` 是 `/v2/business/leader-meeting-review/{report_id}`，不带 `date`。抽取按钮仍带 `tab` 和 `date`。host 为空时不放链接和按钮。

钉钉走 sampleMarkdown。`### 例会复盘｜日期`，然后是总结、完整复盘报告、抽取入口。段落之间用不断行空格，避免空行被折叠。抽取入口一行一个，顺序与飞书按钮相同。

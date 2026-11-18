# 技术规格说明书：CXO 今日重点卡片展示摘要

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-18 |
| 变更窗口 | 2026-11-18 |
| 前置说明书 | `fsd/2026-11-12-cxo-today-highlights.md`（该次没有单独 TSD）；企微卡壳见 `tsd/2026-11-11-wecom-bot-push.md` |
| 对应 FSD | `fsd/2026-11-18-cxo-highlights-card-summary.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/today_highlights、notification |

用户可见行为见对应 FSD。没有新增表、接口或迁移。洞察仍读 `crm_entity_insight`，口径与 2026-11-12 相同。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 摘要行、飞书 / Lark / 钉钉正文 | `backend/app/services/company_today_highlights.py` 的 `render_card_markdown`、`render_dingtalk_markdown` |
| 企微纯文本正文 | 同文件的 `render_card_plain_text`；`backend/app/services/today_highlights_wecom.py` 的 `build_cxo_wecom_template_card` 写入 `sub_title_text` |
| 发卡与运维 / 演示抄送 | `deliver_company_highlight_card`、`_ops_cc_company_today_highlights`、`_demo_cc_company_today_highlights`。三处都用上面的渲染，不另写正文 |
| 企微截断 | `backend/app/platforms/wecom/text_notice.py` 的 `build_text_notice_card` 把 `sub_title_text` 截到 512 字 |
| 单测 | `backend/tests/test_company_today_highlights.py` |

## 2. 正文怎么拼

`handle_company_today_highlights` 组出的 `assembled.sections` 已按槽位和部门排好，条目里有 `summary`、`detail_text`、`department_name`。卡片只读 `summary` 和 `department_name`。

一条摘要：有 `department_name` 时写成「名称：摘要」，冒号用全角「：」；没有名称时只写摘要。`summary` 去掉空白后为空则跳过。`detail_text` 不进入卡片。

飞书、Lark、钉钉按 section 分块，块之间一个空行。块内依次是 `**标题**`、摘要各一行、`[标题](url)`。`sections` 为空时退回 `report_entries` 的一行蓝链，用「｜」分隔。钉钉在这段正文前加 `# 标题`。

企微对每个至少有一条摘要的 section 写成「标题」加摘要行，section 之间一个空行。`jump_list` 仍来自 `report_entries`，整卡 `card_action` 打开第一条。没有摘要的 section 不进正文，入口仍在 `jump_list`。`horizontal_content_list` 仍不写。

## 3. 不改的路径

`pick_report_sections`、`highlights_payload`、`GET /crm/today-highlights/company`、收件人、调度和 `cxo-today` 计费都不改。页面条目继续返回 `summary` 与 `detail_text`。

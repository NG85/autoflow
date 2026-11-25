# 技术规格说明书：复盘和今日重点卡片排版

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-25 |
| 变更窗口 | 2026-11-25 |
| 前置说明书 | 拜访复盘沿 `tsd/2026-11-10-visit-recap-card-default-color.md`；例会复盘沿 `tsd/2026-11-24-leader-meeting-cxo-card.md`；个人今日重点沿 `tsd/2026-11-16-sales-highlights-key-finding-type.md`；CXO 今日重点沿 `tsd/2026-11-18-cxo-highlights-card-summary.md` |
| 对应 FSD | `fsd/2026-11-25-im-card-layout.md` |
| 关联服务 | backend |
| 关联功能模块 | notification、visit_record、crm/leader_meeting_review、crm/today_highlights |

用户可见行为见对应 FSD。没有新表、接口或迁移。例会姓名不写入抽取查询响应。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 飞书、钉钉拜访标题 | `backend/app/services/visit_record_recap_card.py` 的 `recap_card_title`。`build_recap_lite_card` 用它做飞书标题和钉钉 `###` 标题，总长不超过 50 |
| 企微拜访标题 | 同文件 `wecom_recap_title`。去掉「拜访复盘｜」，截到 36 字。`desc` 仍是「拜访复盘」 |
| 钉钉拜访基础信息 | 同文件 `dingtalk_visit_meta_quote`。跟进人和创建时间收成一行引用，商机另起一行 `>`，直接接在 `###` 标题下一行，摘要再直接接在引用下一行。企微 `sub_title_text` 在基础信息和摘要之间用空行 |
| 标题里的质量、日期、客户 | 同文件 `compose_recap_title`。先按剩余字数截断，再拼上「拜访复盘｜」 |
| 例会姓名 | `backend/app/services/leader_meeting_review_card.py` 的 `profile_display_name`，读 `user_profiles.name`。查询失败记警告并当没有名字 |
| 例会人员文案 | 同文件 `meeting_people_text`。同一人只保留负责人。`people_fields` 拆成标签和姓名 |
| 何时写入 | `handle_leader_meeting_review_card` 确认要发之后，把结果放进组装结果的 `people_text`。演示旁路和三端发送都读这一份 |
| 飞书例会卡 | `build_leader_meeting_feishu_card`。人员是第一块正文，总结用 `extra_sections` 另起一块 |
| 企微例会卡 | `build_leader_meeting_wecom_template_card` 把人员放进 `horizontal_facts`。`text_notice.py` 用 `type=0` 横栏，和链接横栏合计不超过 6，人员在前。`sub_title_text` 只留总结 |
| 钉钉例会卡 | `render_dingtalk_markdown` 用 `dingtalk_quote` 把人员直接接在标题下一行，总结直接接在引用下一行。报告链接和抽取蓝链用｜拼成一行，前面留不断行空格与正文隔开 |
| 个人卡飞书正文 | `backend/app/services/sales_today_highlights.py` 的 `_card_line`。`immediate_today` 先写 claim，why_today 用 `>` 引用。出口蓝链前空一行 |
| 个人卡钉钉 | 同文件 `_dingtalk_item_lines` 引用 why_today。`render_dingtalk_markdown` 把关键发现接在正文三个换行之后；有本周蓝链时再接 `---`、`**本周**` 和同一行蓝链 |
| 企微原因行 | `today_highlights_wecom.py` 的 `_wecom_reason_line` 和 `_team_jump_and_horizontal`。引用行去掉 `>` 后加「原因：」。团队卡跳转在「查看 / 更多」后只接昨日日报或上周周报。横栏先放其余报告，再放本周待完成、拜访、例会。周一从拜访对照取出「团队上周拜访」，接到横栏本周待完成后面 |
| 团队卡正文 | `department_today_highlights.py` 的 `_intervention_lines`。顺序是「销售：姓名　　客户 · 商机」、建议动作、`> 情况说明`。飞书出口蓝链前空一行。钉钉正文共用这组行 |
| CXO 板块正文 | `company_today_highlights.py` 的 `_section_bodies`、`render_card_markdown`。只留标题和摘要 |
| CXO 底栏蓝链 | 同文件 `render_card_link_line`。有 sections 时按顺序用 `CARD_LINK_SEPARATOR` 拼接；没有 sections 时仍走 `report_entry_lines` |
| CXO 飞书 / Lark | `_feishu_highlight_card`。正文是板块，`footer` 是底栏。正式发送、运维抄送和演示旁路都走这一份 |
| CXO 钉钉 | `render_dingtalk_markdown`。多个板块之间和底栏前各放一行不断行空格，避免空行被折掉 |
| 单测 | `backend/tests/test_visit_record_recap_card.py`、`backend/tests/test_leader_meeting_review_card_push.py`、`backend/tests/test_sales_today_highlights.py`、`backend/tests/test_department_today_highlights.py`、`backend/tests/test_company_today_highlights.py`、`backend/tests/test_today_highlights_wecom.py` |

## 2. 不改的路径

跟进基础信息卡仍走 `compose_recap_title` 和 `build_visit_basic_wecom_template_card`，灰色说明仍是「拜访记录」。

例会复盘标题仍是「例会复盘｜日期」。企微灰色说明仍是「例会复盘」。没有 `people_text` 时正文仍是总结。例会管理评价把负责人写进自己的 `summary`，不读 `people_text`。抽取查询不返回 `people_text`。

个人卡的关键发现、下周待确认、飞书按钮和企微横向入口位置不变。嵌在团队卡里的个人区块不含本周按钮。飞书和钉钉上，团队卡自己的「团队本周」、日报或周报、拜访对照仍按原位置。企微把日报或周报放进跳转，本周入口留在横栏并排在报告后面。周一的「团队上周拜访」不留在企微正文，改放到横栏本周任务后面。本周链接的筛选条件不变。页面查询仍返回完整条目。

CXO 的 `pick_report_sections`、摘要行格式、企微 `jump_list`、收件人和计费都不改。

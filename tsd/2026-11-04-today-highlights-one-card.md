# 技术规格说明书：今日重点一人一天一张卡

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-04 |
| 变更窗口 | 2026-11-04 |
| 前置说明书 | `tsd/2026-10-27-leader-insight-reports.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |

本文件描述一人一天一张今日重点卡的 backend 实现。用户可见行为见 `fsd/2026-11-04-today-highlights-one-card.md`。前端接入见 `backend/docs/workbench-today-highlights-frontend.md`。历史技术说明书不改。没有新增表或迁移。计费场景仍见 `tsd/2026-10-27-today-highlights-billing.md`。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 团队卡装配、折入立即处理、日报链接、发送成功的 oauth id | `backend/app/services/department_today_highlights.py` |
| 一线卡标题、折入区块、排除已收团队卡的人 | `backend/app/services/sales_today_highlights.py` |
| 合并定时任务先团队、后一线 | `backend/app/tasks/cron_jobs.py` 的 `push_crm_today_highlights` |
| 团队日报 URL | `backend/app/utils/push_page_urls.py` 的 `build_team_daily_report_url` |
| 推送说明书 | `backend/docs/notification-scenes.md` |
| 单测 | `backend/tests/test_department_today_highlights.py`、`backend/tests/test_sales_today_highlights.py`、`backend/tests/test_leader_insight_reports.py`、`backend/tests/test_push_page_urls.py` |

`POST /notification/push` 的两个处理函数仍各自调用，不传排除名单。

## 2. 定时任务

`_HIGHLIGHT_AUDIENCES` 仍是 `sales`、`department`。不传 `audience` 时先跑 department，再跑 sales。

`handle_department_today_highlights` 成功送达的负责人 oauth id 放进 `sent_oauth_user_ids`。发送失败、跳过、查不到 oauth id 的不放。`_push_one_today_highlights` 把这份名单传给 `handle_sales_today_highlights(..., exclude_oauth_user_ids=...)`。

销售侧按 `subject_id` 去掉这些人，条目 `skip_reason=covered_by_team_card`，不发送、不上报个人用量。全部被排除且没有其他人时，汇总 `skip_reason` 为 `covered_by_team_card`。

一边抛异常时返回空的已送达名单和 `push_failed`，不重试。因此团队任务失败后，一线任务仍会发给包括负责人在内的所有人。只推 `audience=sales` 时排除名单为空。

## 3. 折入立即处理

空团队内容在折入之前就 `continue`。因此个人抽取单独不会产生团队卡。

通过空内容检查之后调用 `personal_highlights_for_leader`：

1. `sales_daily.today_highlights` 关闭则返回空。
2. `load_sales_extract_rows` 只读该 oauth id 当天的一线抽取。
3. `assemble_sales_highlights` 没有内容则返回空。
4. `filter_opted_out_recipients` 使用 `scene=sales_daily`、`variant=today_highlights`、`department_ids=[""]`。本人关掉则返回空。
5. 读抽取、装配或关订检查抛错时记日志并返回空，不挡住团队卡。
6. 不调用个人额度检查。

结果放在 `card["personal_highlights"]`。飞书用 `render_embedded_personal_markdown`，钉钉用 `render_embedded_personal_dingtalk`。标题常量 `EMBEDDED_PERSONAL_TITLE` 为「立即处理」。只展开 `immediate_today` 的第一条，出口仍是一线今日重点页。`card_blue_links` 保留建议关注和下周待确认。不调用 `card_action_buttons`。

团队卡成功后仍只 `report_today_highlights_usage("department", ...)`。被排除的人不会进入销售发送循环，因此不会再记 `sales`。

有抽取的负责人桶写 `oauth_user_id=subject_id`。只有拜访或报告的桶没有这个字段，发送前用 `leader_oauth_id` 补。补不到就不进 `sent_oauth_user_ids`，一线卡仍可发。

## 4. 卡片结构

标题常量：一线 `SALES_CARD_TITLE` 为「我的今日重点」，团队 `TEAM_CARD_TITLE` 为「团队今日重点」。

`render_card_sections` 三块：立即处理、团队内容、本周标题。团队内容里需介入和拜访对照在前，重点商机、团队人效、经营趋势和周二到周日的日报写成同一行蓝链。飞书有多块时首块作正文，其余进 `extra_sections`，块与块之间加分隔线。

`_report_card_lines` 的 `has_other_blocks` 只看需介入或拜访对照，不看立即处理。为假且有报告链接时，块首是 `_reports_only_heading`（「今日需介入」）再加原来的 `REPORTS_ONLY_LEAD`。钉钉标题用 `##`，飞书用加粗。

钉钉把立即处理和个人以外的正文分成两段。两段都有时，中间插入 `---`。本周按钮前原有的 `---` 保留。

`_team_report_line` 用 `report_date - 1 day` 调用 `build_team_daily_report_url`。路径是 `/v2/business/reports?tab=daily-team&date=`。host 为空或没有业务日时返回空串，不占位。它跟在报告入口后面，同一行，不是 `card_footer_buttons` 的一项。

## 5. 相关销售姓名

`_serialize_item` 先用档案姓名。档案没有、且 `related_user_id` 匹配 `[\u4e00-\u9fff]` 时，把该字符串写入 `related_user_name`。卡片渲染仍是姓名优先，否则回退 `related_user_id`。

## 6. 测试

- 团队卡标题、折入顺序、不计个人用量、销售关订时不出现立即处理、发送失败不写入 `sent_oauth_user_ids`。
- 日报链接日期为业务日前一天，且不出现在本周按钮里。它和报告入口写在团队内容同一行。
- 一线卡标题、折入区块不含本周按钮、`covered_by_team_card`、团队任务失败后一线仍发送。
- 合并任务先部门后销售；只推销售时排除名单为空。
- 只有报告时，飞书和钉钉都以「今日需介入」起头。

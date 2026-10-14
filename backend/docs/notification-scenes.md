# 推送场景：资格路由 + 默认可收 + 可关掉

销售个人日报、公司/部门日周报 **都不是开放订阅**。身份决定谁有资格；有资格的人默认接收，可以自己关掉。关掉不会把名单让给别人。同一槽位可以配置不同报告形态（统计卡 / 周拜访报告），各形态独立开关。公司/部门今日重点是独立 scene，不跟对应日报共用资格集。

相关代码：

- 场景目录：`app/services/notification_scene_catalog.py`
- 日周报变体：`app/services/report_push_policy.py`（SiteSetting `report_push_policy`）
- 预览：`GET /notification/scenes`（`notification:scenes:view`）、`GET /notification/preview`（`notification:scenes:preview`）
- 用户开关：`GET/PUT /notification/preferences`
- 日拜访报告 Markdown：`POST /notification/push` `type=daily_visit_report`（读 `crm_department_daily_summary`；公司/部门）
- 一线销售今日重点：`POST /notification/push` `type=sales_today_highlights`（读 `crm_skill_extract_items`；仅本人）
- 销售负责人今日重点：`POST /notification/push` `type=department_today_highlights`（读部门粒度 `TODAY_INTERVENTION`；仅部门负责人，不进群）
- 周拜访报告 Markdown：`POST /notification/push` `type=weekly_visit_report`（读 `crm_weekly_followup_summary.report_kind=visit_report`；公司/部门）
- 偏好表：`notification_delivery_preferences`

拜访卡、未参加的 review 仍是实例路由，不加这种偏好层。旁观继续走 `notification_cc_rules`。群聊不走个人 opt-out。

---

## 资格从哪来

| scene | routing | preference | 谁有资格 | 用户能开关的 |
|-------|---------|------------|----------|----------------|
| `sales_daily` | instance | eligible_opt_out | 本人（OAuth `notification:daily_report_personal:receive`） | 关掉/打开自己的日报（含今日重点变体） |
| `department_daily` / `department_weekly` | eligible_set | eligible_opt_out | 该部门 leader + team receive；群走 `department_group_chats` | 只对自己负责的部门关/开 |
| `company_daily` / `company_weekly` | eligible_set | eligible_opt_out | OAuth `*:company:receive`（`kpi_card`） | 关掉/打开自己的公司日或周 |
| `company_daily` 的 `summary_md` | 同公司日报 | eligible_opt_out | 未指定时与定时任务相同（OAuth `notification:daily_report_company:receive`）；`recipient_user_ids` 指定后只发给这些人 | 关掉/打开这份 Markdown |
| `department_daily` 的 `summary_md` | 同部门日报 | eligible_opt_out | 该部门 leader + team receive；群走 `department_review`；`recipient_user_ids` 可覆盖 | 同上 |
| `department_highlights` | eligible_set | eligible_opt_out | 该部门负责人（不进群、不过 team receive） | 只对自己负责的部门关/开今日重点 |
| `company_highlights` | eligible_set | eligible_opt_out | `report_push_policy` 的 `recipient_user_ids`（指定接收人，**不是**公司日报名单） | 关掉/打开公司今日重点 |
| `today_highlights`（`sales_daily` 的变体） | 同销售日报 | eligible_opt_out | 本人 | 同上 |
| `weekly_visit_report`（公司/部门周报的 `visit_report` 变体） | 同槽位周报 | eligible_opt_out | 与同槽位周报同一资格集 | 同上 |
| `visit_record` | instance | none | 录入人 / 协同 / 上级 / cc / 群 | 无；旁观用 cc_rules |
| `review_session` | instance | none | CRMReviewAttendee | 无 |

实际个人收件人 = **资格名单 − 该 scene/variant/部门上 opted_out 的人**。无资格的 toggle 返回 403。

变体上的 `recipient_user_ids`：日周报 Markdown（`summary_md` / `visit_report`）未写时与对应定时任务同一套收件人；写了就只发给这些人，部门群也不再替代个人（预览 `reasons` 含 `variant_override`）。`company_highlights` 上这就是资格集本身（预览 `named_recipient`），未指定则无人有资格。名单上的人默认接收，同样可以关掉。

---

## 预览

```
GET /notification/preview?scene=sales_daily&variant=kpi_card
GET /notification/preview?scene=company_weekly&variant=kpi_card
GET /notification/preview?scene=company_daily&variant=summary_md
GET /notification/preview?scene=department_daily&variant=summary_md&department_id=...
GET /notification/preview?scene=department_weekly&variant=visit_report&department_id=...
GET /notification/preview?scene=company_highlights
GET /notification/preview?scene=department_highlights&department_id=...
GET /notification/preview?scene=visit_record&variant=visit_card&record_id=...
```

需登录，且当前用户具备 OAuth `notification:scenes:preview`。不发送。返回 `eligible[]`、`will_send[]`（有 open_id 且未 opt-out）、`opted_out[]`、`groups[]`、`enabled`、跳过原因。资格解析复用现有 `get_recipients_for_*`。

目录 `GET /notification/scenes` 需 `notification:scenes:view`。这两码是功能门控，**不是** receive 资格；有 `*:receive` 不代表能看名单。

---

## 用户偏好

表 `notification_delivery_preferences`：`user_id + scene + variant + department_id`，`receive=false` 表示关掉。无行 = 接收。

- `variant` 空：该 scene 下全部变体
- `department_id` 空：公司级；部门 scene 上表示关掉自己负责的全部部门

`GET /notification/preferences` 只列出 **自己有资格** 的项（销售只出现自己的日报及今日重点变体；部门 leader 会出现部门日/周报和 `department_highlights`；写进 `company_highlights.recipient_user_ids` 的人才出现公司今日重点；写进 `company_daily.summary_md.recipient_user_ids` 的人才出现公司日拜访报告 Markdown）。`PUT` 无资格 403。

发送销售个人日报、公司/部门日周报的个人通道时会扣掉 opted_out；部门群仍按群配置发。

---

## 日/周报变体（report_push_policy）

SiteSetting 名：`report_push_policy`。未配置则各报告槽位仅 `kpi_card: true`；今日重点槽位默认关闭。

槽位含报告：`sales_daily`、`company_daily` / `department_daily`、`company_weekly` / `department_weekly`；今日重点：`company_highlights` / `department_highlights`（销售个人今日重点仍写在 `sales_daily.today_highlights`）。

```yaml
report_push_policy:
  sales_daily:
    kpi_card: true
    today_highlights: false
  company_daily:
    kpi_card: true
    summary_md:
      enabled: true
      recipient_user_ids: ["<admin_user_uuid>"]
  department_daily:
    kpi_card: true
    summary_md: false
  company_highlights:
    today_highlights:
      enabled: true
      recipient_user_ids: ["<cxo_user_uuid>"]
  department_highlights:
    today_highlights: true
  company_weekly:
    kpi_card: false
    visit_report: true
  department_weekly:
    kpi_card: true
    visit_report:
      enabled: true
      recipient_user_ids: ["<user_uuid>"]
```

- `kpi_card` 关闭：定时任务仍跑统计，跳过发卡（销售个人日报、公司/部门日周报均适用）。
- `today_highlights`：
  - 销售个人：仍是 `sales_daily` 的变体，资格=本人。Cronicle 调 `POST /notification/push` `type=sales_today_highlights`，或由独立定时任务 `push_crm_today_highlights` 调用同一处理。`CRM_TODAY_HIGHLIGHTS_ENABLED` 控制是否挂上 beat，`CRM_TODAY_HIGHLIGHTS_CRON` 控制时刻，与日报任务、计费场景、`CRM_DAILY_REPORT_FEISHU_ENABLED` 无关。指定了 `report_date` 就用这一天，没指定则用北京时间当天。读 `crm_skill_extract_items` 里 `subject_type=USER`、`audience=sales` 的有效抽取。是否发出由 `sales_daily.today_highlights` 决定。卡片正文只放置信度最高的一条立即处理，其余立即处理在正文后写成「更多（N）」蓝链。商机风险是蓝链，周五再加下周待确认蓝链。「本周待完成」每天都有，打开任务页并筛本人、预计完成日在本周一到本周日、状态未完成（PENDING / IN_PROGRESS，含已逾期）的任务。周一不放「本周已拜访」；周二到周日加这个按钮，打开已录入的拜访列表并筛本人、日期在本周一到本周日。`unchanged` 风险不进卡。三个区块都空则跳过，不发「今天没有重点」。可选 `recipient_user_ids`（OAuth id）；不传则发给当天有条目的全部一线销售。变体关闭、本人关掉、没有 open_id 都 skip（HTTP 仍 200）。不进群、不抄送运维。结构化页数据：`GET /crm/sales/today-highlights`，只返回当前登录人。
  - 部门：独立 scene `department_highlights`，只推部门负责人，**不进** `department_review` 群，也不走团队日报的 receive 过滤。Cronicle 调 `POST /notification/push` `type=department_today_highlights`，或由同一独立定时任务 `push_crm_today_highlights` 调用。与部门日报的分支和群推送无关。指定了 `report_date` 就用这一天，没指定则用北京时间当天。只发给部门负责人本人，不进部门群。`report_date` 不传则北京时间当天。读 `crm_skill_extract_items` 里 `subject_type=DEPARTMENT`、`audience=leader` 的 `TODAY_INTERVENTION`。`subject_id` 是 `department_mirror.unique_id`，换成部门名后走已有的部门负责人解析（OAuth 失败再回退档案负责人）。本部门没有负责人时只上探一级，交给上级部门负责人，并进该负责人同一张卡，条目带上原部门名；上级也没有负责人则跳过。有负责人但关掉或没有 open_id 时不上探。是否发出由 `department_highlights.today_highlights` 决定。同一负责人一天只收一张卡，标题是「今日重点｜日期」，不写部门名。正文只放置他未关掉的部门里置信度最高的一条介入（销售姓名、事项、负责人动作、客户/商机名）；负责多个部门时这条带上部门名。其余紧跟「更多（N）」，链到 `/v2/department-highlights?date=&tab=leader_intervene`，不带 `department_id`。快照标题「今日需介入」写在正文标题上。周一只展示需介入；周二到周日再加「本周已拜访」按钮，打开拜访列表并只筛本周一到本周日的拜访日期，可见范围由拜访列表权限决定。当天没有介入项则跳过，不发空卡。可选 `department_id`，指定后这张卡只含该部门；不传则按人归并。变体关闭、负责人关掉、没有 open_id 都 skip（HTTP 仍 200）。不抄送。重复调用会再发，由该定时任务或 Cronicle 一天一次。结构化页数据：`GET /crm/departments/today-highlights`，只返回当前用户负责的部门。
  - 公司：独立 scene `company_highlights`，资格仅为配置的 `recipient_user_ids`，**不走** OAuth、**不复用**公司日报名单。未指定接收人则谁都不发。发送路径仍未接。
  - 目录与策略已预留，默认 `enabled: false`。
- `visit_report` 仅周报槽位。Cronicle / 外部服务 `POST /notification/push` `type=weekly_visit_report`：服务端按 `week_start`/`week_end`（都空则上一完整周）读 `crm_weekly_followup_summary`（`report_kind=visit_report`）的 `summary_content`，再按策略发 Markdown（飞书无模板卡 / post）。公司周报同样按 `OPS_CC_*` 抄送运维后门一次，部门周报不抄送。部门不传 `department_id`/`department_name` 时，部门范围与周报定时任务一致：OAuth 部门负责人（为空时回退本地档案里可推送的负责人）或配置了部门日报群的部门，按部门名称匹配汇总行。没有 Markdown 正文时仍发一条与定时任务相同的无记录说明，且不抄送运维。无名单 / 变体关闭会 skip（HTTP 仍 200）。**没有**独立的 `agent_markdown` 通道。
- `summary_md` 仅日报槽位（`company_daily` / `department_daily`）。Cronicle 调 `POST /notification/push` `type=daily_visit_report`：服务端按 `report_date`（默认北京时间昨天）读 `crm_department_daily_summary` 的 `summary_content`。公司未写 `recipient_user_ids` 时与定时任务相同（OAuth 公司日报资格），写了则只发给指定人；发出时按 `OPS_CC_*` 抄送运维后门一次。部门未指定时走负责人，配了 `department_review` 群则只发群；`recipient_user_ids` 指定后只发给这些人、不再发群。部门不抄送运维。没有 Markdown 正文时仍发一条与定时任务相同的无记录说明，且不抄送运维。部门不传部门字段则部门范围与日报定时任务一致：OAuth 里有负责人的部门，或配置了部门日报群的部门，按部门名称匹配汇总行。无名单 / 变体关闭会 skip。
- 变体关了谁都不发；变体开了再按资格 ∩ 未关掉。

销售个人日报：形态开关与个人偏好正交——关统计卡则本人也收不到卡；开着统计卡时，本人仍可把自己的日报（或今日重点）关掉。公司/部门今日重点与对应日报的关订互不影响。

`weekly_visit_report` 字段：`scene`（company_weekly / department_weekly）、可选 `week_start`/`week_end`（都空则上一完整周），部门可选 `department_id` 或 `department_name`。不落库，只读已有 `visit_report` 行。

`daily_visit_report` 字段：`scene`（company_daily / department_daily）、`variant`（默认 `summary_md`）、可选 `report_date`（YYYY-MM-DD，默认北京昨天）、可选 `title` / `delivery` / 部门字段。Cronicle 示例：

```json
{"type": "daily_visit_report", "scene": "company_daily"}
{"type": "daily_visit_report", "scene": "department_daily"}
{"type": "sales_today_highlights"}
{"type": "department_today_highlights"}
{"type": "weekly_visit_report", "scene": "company_weekly"}
{"type": "weekly_visit_report", "scene": "department_weekly"}
```

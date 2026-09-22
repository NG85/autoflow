# 推送场景：资格路由 + 默认可收 + 可关掉

销售个人日报、公司/部门日周报 **都不是开放订阅**。身份决定谁有资格；有资格的人默认接收，可以自己关掉。关掉不会把名单让给别人。同一槽位可以配置不同报告形态（统计卡 / 周拜访报告），各形态独立开关。公司/部门今日重点是独立 scene，不跟对应日报共用资格集。

相关代码：

- 场景目录：`app/services/notification_scene_catalog.py`
- 日周报变体：`app/services/report_push_policy.py`（SiteSetting `report_push_policy`）
- 预览：`GET /notification/scenes`（`notification:scenes:view`）、`GET /notification/preview`（`notification:scenes:preview`）
- 用户开关：`GET/PUT /notification/preferences`
- 日拜访报告 Markdown：`POST /notification/push` `type=daily_visit_report`（读 `crm_department_daily_summary`；公司/部门）
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
| `company_daily` 的 `summary_md` | eligible_set | eligible_opt_out | `report_push_policy.company_daily.summary_md.recipient_user_ids`（指定人，**不是** OAuth 公司日报名单） | 关掉/打开这份 Markdown |
| `department_daily` 的 `summary_md` | 同部门日报 | eligible_opt_out | 该部门 leader + team receive；群走 `department_review`；`recipient_user_ids` 可覆盖 | 同上 |
| `department_highlights` | eligible_set | eligible_opt_out | 该部门负责人（不进群、不过 team receive） | 只对自己负责的部门关/开今日重点 |
| `company_highlights` | eligible_set | eligible_opt_out | `report_push_policy` 的 `recipient_user_ids`（指定接收人，**不是**公司日报名单） | 关掉/打开公司今日重点 |
| `today_highlights`（`sales_daily` 的变体） | 同销售日报 | eligible_opt_out | 本人 | 同上 |
| `weekly_visit_report`（公司/部门周报的 `visit_report` 变体） | 同槽位周报 | eligible_opt_out | 与同槽位周报同一资格集 | 同上 |
| `visit_record` | instance | none | 录入人 / 协同 / 上级 / cc / 群 | 无；旁观用 cc_rules |
| `review_session` | instance | none | CRMReviewAttendee | 无 |

实际个人收件人 = **资格名单 − 该 scene/variant/部门上 opted_out 的人**。无资格的 toggle 返回 403。

变体上的 `recipient_user_ids`：报告槽位的 `kpi_card` / `visit_report` / `department_daily.summary_md` 是管理员 **覆盖** 资格集（预览 `reasons` 含 `variant_override`）；`company_highlights` 与 `company_daily.summary_md` 上这就是资格集本身（预览 `named_recipient`）。名单上的人默认接收，同样可以关掉。未指定则无人有资格。

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
  - 销售个人：仍是 `sales_daily` 的变体，资格=本人。
  - 部门：独立 scene `department_highlights`，只推部门负责人，**不进** `department_review` 群。
  - 公司：独立 scene `company_highlights`，资格仅为配置的 `recipient_user_ids`，**不走** OAuth、**不复用**公司日报名单。未指定接收人则谁都不发。
  - 发送路径后续再接；目录与策略已预留，默认 `enabled: false`。
- `visit_report` 仅周报槽位。Cronicle / 外部服务 `POST /notification/push` `type=weekly_visit_report`：服务端按 `week_start`/`week_end`（都空则上一完整周）读 `crm_weekly_followup_summary`（`report_kind=visit_report`）的 `summary_content`，再按策略发 Markdown（飞书无模板卡 / post）。部门不传 `department_id`/`department_name` 时推该周全部部门行。无内容 / 无名单 / 变体关闭会 skip（HTTP 仍 200）。**没有**独立的 `agent_markdown` 通道。
- `summary_md` 仅日报槽位（`company_daily` / `department_daily`）。Cronicle 调 `POST /notification/push` `type=daily_visit_report`：服务端按 `report_date`（默认北京时间昨天）读 `crm_department_daily_summary` 的 `summary_content`。公司走该变体 `recipient_user_ids`（不是 OAuth 公司日报名单）；部门走负责人 + `department_review` 群（`recipient_user_ids` 可覆盖）。部门不传部门字段则推该日全部部门行。无内容 / 无名单 / 变体关闭会 skip。
- 变体关了谁都不发；变体开了再按资格 ∩ 未关掉。

销售个人日报：形态开关与个人偏好正交——关统计卡则本人也收不到卡；开着统计卡时，本人仍可把自己的日报（或今日重点）关掉。公司/部门今日重点与对应日报的关订互不影响。

`weekly_visit_report` 字段：`scene`（company_weekly / department_weekly）、可选 `week_start`/`week_end`（都空则上一完整周），部门可选 `department_id` 或 `department_name`。不落库，只读已有 `visit_report` 行。

`daily_visit_report` 字段：`scene`（company_daily / department_daily）、`variant`（默认 `summary_md`）、可选 `report_date`（YYYY-MM-DD，默认北京昨天）、可选 `title` / `delivery` / 部门字段。Cronicle 示例：

```json
{"type": "daily_visit_report", "scene": "company_daily"}
{"type": "daily_visit_report", "scene": "department_daily"}
{"type": "weekly_visit_report", "scene": "company_weekly"}
{"type": "weekly_visit_report", "scene": "department_weekly"}
```

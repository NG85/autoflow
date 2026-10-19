# Autoflow 技术规格说明书（历史版本）

| 要素 | 内容 |
| --- | --- |
| 作者 | nagao |
| 时间 | 2026-10-19 |
| 代码基线 | `1582acf1`（2026-10-10） |
| 关联服务 | backend、core；TiDB；OAuth；飞书 / Lark / 钉钉；Celery；Aldebaran |
| 关联功能模块 | auth、chat、knowledge-base、retrieve、evaluation、crm/visit_records、crm/review、crm/weekly_followup、crm/documents、crm/views、crm/config、reports、notification、contacts、writeback、billing |

本文件描述 `1582acf1` 的实现落点。2026-10-12 及之后的实现见 `tsd/2026-10-19-release.md`。

## 1. 仓库怎么拆

| 目录 | 职责 |
| --- | --- |
| `backend` | FastAPI 服务。CRM、通知、知识库管理 API、Celery 任务都在这里。入口 `backend/main.py`，路由在 `backend/app/api`。 |
| `core` | `autoflow` 库：索引、检索、知识图谱和模型配置，供 backend 调用。 |

HTTP API 挂在 `/api/v1`。CRM 子路由在 `backend/app/api/routes/crm/compose.py` 按固定顺序拼装，子路由自己写完整 `/crm/...` 路径。

## 2. 身份

- OAuth BFF：`backend/app/api/routes/auth_oauth.py`，会话和权限查询走 `backend/app/services/oauth_service.py`。
- 旧用户名密码登录：`backend/app/api/routes/auth_legacy_login.py`。
- 当前用户由 `CurrentUserDep` 注入。业务代码用 `user_profiles` 把本地用户对到 `oauth_user_id`、部门、汇报关系和平台 `open_id`。
- 公司报告接收资格看 OAuth 权限码。部门接收人由部门负责人、团队接收配置和群配置决定。

## 3. 数据

业务数据在 TiDB。CRM 主数据包括客户、联系人、商机、订单、拜访 `crm_sales_visit_records` 及其修订。拜访复盘洞察在 `crm_entity_insight`，拜访后抽取在 `crm_postvisit_extract_items`。

基线模型里，`crm_entity_insight.entity_type` 为 `ACCOUNT`、`OPPORTUNITY`、`VISIT`。拜访复盘用 `entity_type=VISIT`，销售视角 `insight_type=POSTVISIT_REVIEW_SALES_VIEW`。代码按 `POSTVISIT_REVIEW_LEADER_VIEW` 读上级视角。

日周报读取上游已经写好的统计和摘要，例如 `crm_department_daily_summary`、`crm_weekly_followup_summary`。Autoflow 不在请求路径上现算这些摘要。

知识库使用文档、分块、实体、关系，以及按知识库隔离的表。向量存 TiDB VECTOR。图谱编辑写入 `staff_action_log`。

`crm_data_authority` 由其他系统维护，本服务只读，用于客户、商机等范围。本地联系人权限不读这张表。拜访保存时，`backend/app/crm/dynamic_fields.py` 按配置生成动态字段。

通知偏好在 `notification_delivery_preferences`，键为用户、场景、变体、部门。没有行表示接收。抄送规则在 `notification_cc_rules`。

场景目录：`backend/app/services/notification_scene_catalog.py`。日周报开关和变体：站点设置 `report_push_policy`，由 `backend/app/services/report_push_policy.py` 读取。未配置时，报告槽位只打开 `kpi_card`。目录中已声明 `today_highlights`、`department_highlights`、`company_highlights`，基线没有对应的读取和发送实现。

## 4. 拜访复盘落点

- 路由：`backend/app/api/routes/crm/visit_records/router.py`
- 视角选择：`backend/app/services/visit_record_recap.py`、`backend/app/services/visit_record_insight_reader.py`
- 记录人始终看销售视角。汇报链上级看上级视角。其余协同人看销售视角。其他人看上级视角。抽取只查记录人本人，`CONCLUSION` 也回传 `status`。
- 视角函数在 `visit_record_insight_reader.py`：`recap_view_for_viewer` 用于详情，`recap_view_for_role` 用于推送。这个基线没有 `has_recap`。
- 推送卡片、收件人和抄送：`visit_record_push_policy.py`、`visit_record_push_recipients.py`、`visit_record_cc_resolver.py`、`visit_record_recap_card.py`。

## 5. 通知与定时任务

发送统一走 `backend/app/services/platform_notification_service.py`，平台为飞书、Lark、钉钉。

Celery 在 `backend/app/celery.py` 注册。开关打开后注册的定时任务：

| 任务 | 开关 | 默认定时 |
| --- | --- | --- |
| `create_crm_daily_datasource` | `CRM_DAILY_TASK_ENABLED` | 每天 10:00 |
| `sync_bitable_visit_records` | `ENABLE_FEISHU_BTABLE_SYNC` | 每周日 13:00。cron 无法解析时退到每天 00:05 |
| `generate_crm_daily_statistics` | `CRM_DAILY_REPORT_ENABLED` | 每天 08:30 |
| `generate_crm_weekly_report` | `CRM_WEEKLY_REPORT_ENABLED` | 每周日 11:00 |
| 周跟进摘要，部门与公司各一次 | `CRM_WEEKLY_FOLLOWUP_ENABLED` | 每周日 09:30 / 09:40 |
| 负责人参与度 | `CRM_WEEKLY_FOLLOWUP_ENGAGEMENT_ENABLED` | 周一 09:00 |
| `crm_visit_records_writeback` | 配置了 `CRM_WRITEBACK_DEFAULT_MODE` 且 cron 为合法 5 段 | `CRM_WRITEBACK_CRON`；窗口由 `CRM_WRITEBACK_FREQUENCY` 决定 |
| `send_sales_task_summary` | `CRM_SALES_TASK_ENABLED` | 每周日 10:00 |
| `rebuild_crm_visit_metrics` | `CRM_VISIT_METRICS_ENABLED` | 每小时整点 |
| `rebuild_crm_todo_metrics` | `CRM_TODO_METRICS_ENABLED` | 每小时第 5 分钟 |
| `persist_crm_todo_facts_hourly_stock` | `CRM_TODO_FACTS_HOURLY_ENABLED`，默认关闭 | 每小时第 10 分钟 |

手动触发在 `backend/app/api/admin_routes/crm_task.py`、`crm_visit_metrics.py`、`crm_todo_metrics.py`，要求超级管理员。任务实现集中在 `backend/app/tasks/cron_jobs.py`。回写实现在 `backend/app/services/crm_writeback_service.py`。多维表格导入在 `backend/app/tasks/bitable_import.py`。

个人通道发送前调用 `filter_opted_out_recipients`。部门群不走个人 opt-out。`variant` 为空的关闭记录覆盖该 scene 的全部变体。

## 6. 拜访链接、周报与计费

- 钉钉听记：`backend/app/tasks/dingtalk_transcribe.py`。HTTP 请求里同步写 AI 表格，异步任务再轮询 enrichment 并通知 Aldebaran。
- 其他链接拜访：`backend/app/tasks/link_visit_enrichment.py`。原文在请求里落库，异步任务做 LLM enrichment。会议纪要在 `backend/app/services/meeting_summary_service.py`，与风险抽取并行。
- 客户文档问答抽取：`backend/app/tasks/document_qa.py`。
- 部门、公司周报查询调用 `backend/app/services/aldebaran_service.py`。销售四象限由 `backend/app/services/sales_quadrants_service.py` 在日报统计中读取。
- 飞书计费门面：`backend/app/services/feishu_billing_facade.py`。场景包括拜访、对话、复盘对话、三类日报、团队周报和周跟进摘要。用量上报结果写入 `feishu_billing_usage_report`，写库失败不影响主流程。

## 7. 知识库与对话

- 管理 API：`backend/app/api/admin_routes/knowledge_base/`、`llm`、`embedding_model`、`reranker_model`、`evaluation`
- 对话 API：`backend/app/api/routes/chat.py`、`chat_engine.py`
- 检索 API：`backend/app/api/routes/retrieve/`
- 索引任务：`backend/app/tasks/build_index.py`、`build_crm_index.py`、`build_playbook_index.py`、`knowledge_base.py`
- 图谱与检索实现在 `core/autoflow`，由上述 backend 管理 API 和索引任务调用。

## 8. 基线边界

以下能力的代码落点不在 `1582acf1`：

- 复盘只对记录人和其汇报链上级开放，以及列表 `has_recap`
- `CONCLUSION` 抽取对外不回传 `status`
- `crm_skill_extract_items` 的读取与赞踩、采纳回写
- 今日重点查询路由和独立推送任务
- `crm_subject_interactions`
- 系统管理员阅读上级视角复盘
- 按库内实际值 `POSTVISIT_REVIEW_LEAD_VIEW` 读取上级复盘
- `crm_entity_insight.period_key` 的模型字段

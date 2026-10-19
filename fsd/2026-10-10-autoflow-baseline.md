# Autoflow 功能规格说明书（历史版本）

| 要素 | 内容 |
| --- | --- |
| 作者 | nagao |
| 时间 | 2026-10-19 |
| 代码基线 | `1582acf1`（2026-10-10） |
| 关联服务 | backend、core；TiDB；OAuth；飞书 / Lark / 钉钉；Celery |
| 关联功能模块 | auth、chat、knowledge-base、retrieve、evaluation、crm/visit_records、crm/review、crm/weekly_followup、crm/documents、crm/views、crm/config、reports、notification、contacts、writeback、billing |

本文件描述 2026-10-12 之前已经交付的行为。2026-10-12 及之后的变更见 `fsd/2026-10-19-release.md`。

## 1. 产品范围

Autoflow backend 提供销售 CRM 与知识库对话的 API。销售侧覆盖拜访记录、拜访复盘、周跟进、日周报推送、客户文档和 CRM 视图。知识库侧覆盖文档入库、向量与图谱索引、对话和管理员配置接口。

使用角色：

- 一线销售：录入和查看自己的拜访，看自己的复盘抽取，收个人日报。
- 汇报链上级：看下级拜访的上级视角复盘，收所辖部门的日周报。
- 具备公司接收权限的人：收公司日周报。
- 系统管理员：管理模型、知识库、站点配置，并手动触发 CRM 统计与推送任务。
- 对话用户：在已配置的 Chat Engine 上提问。

通知场景目录里已经有「今日重点」变体名称和偏好槽位。基线不提供今日重点查询接口、抽取列表，也不单独发卡。

## 2. 身份与会话

- 登录走 OAuth BFF：`POST /login/oauth`、`POST /logout/oauth`。旧登录 `POST /login`、`POST /logout` 仍保留。
- 注册：`POST /users/register`。
- 当前用户：`GET /users/me`。菜单：`GET /me/menu-config`。超级管理员可搜索用户：`GET /admin/users/search`。
- 用户可创建、列出、删除自己的 API Key。
- 健康检查：`GET /healthz`、`GET /healthz/oauth`。站点公开配置：`GET /site-config`。系统启动检查：`GET /system/bootstrap-status`，返回 RAG 必需配置、可选配置，以及配置是否还要迁移。

健康检查、站点公开配置、系统启动检查和注册不要求登录。其余业务接口需要登录。OAuth 权限码决定能否接收公司或个人报告，不代替「是不是这条拜访的记录人」。

## 3. 拜访记录

模块：`crm/visit_records`。

销售可以：

- 新建拜访：`POST /crm/visit_record`。
- 校验拜访：`POST /crm/visit_record/verify`。
- 按条件查询、导出，并读取筛选项。
- 打开单条详情、修改，并查看修订历史。
- 对拜访写评论。
- 同步分贝通签到：`POST /crm/fenbeitong/checkins`。
- 查询每日客户跟进：`POST /crm/daily_customer_followups/query`。

拜访还有这些进入方式：

- 飞书多维表格同步。`ENABLE_FEISHU_BTABLE_SYNC` 打开后，按 `FEISHU_BTABLE_SYNC_CRON` 导入，默认每周日 13:00。cron 无法解析时退到每天 00:05。
- 带链接的拜访在请求里先落原文。钉钉听记再异步轮询 AI 表格和文档 enrichment，完成后通知 Aldebaran 推卡。其他链接拜访异步做风险抽取和会议纪要，再通知推卡。
- 保存拜访时按动态字段配置生成 `dynamic_fields`。

拜访复盘：

- `GET /crm/visit_records/{record_id}/recap` 按当前用户选视角。没有复盘行时不查汇报链，也不读抽取表。洞察或抽取读取失败时对应字段为空，接口仍返回 200。
- 记录人始终看销售视角 `POSTVISIT_REVIEW_SALES_VIEW`。本条汇报链上级看上级视角，即使同时是协同人。其余协同人看销售视角。其他人看上级视角。
- 上级视角按 `POSTVISIT_REVIEW_LEADER_VIEW` 读取。汇报链有结果时用 open_id 对齐；链为空或查询失败时退回 `direct_manager_id`。
- 推送侧：记录人、协同人看销售视角；上级、抄送看上级视角；群一律上级视角。
- 结构化抽取只给记录人本人。各类抽取都回传 `status`，包括 `CONCLUSION`。赞或踩只返回当前用户自己的。记录人可以对抽取做赞踩和采纳或拒绝。
- 列表没有 `has_recap`。

拜访卡按实例推送：录入人、协同人、上级、抄送规则和群。个人偏好开关不作用于拜访卡。

## 4. 销售复盘会

模块：`crm/review`。

参加复盘会的人可以：

- 打开自己最近一场和历史场次。
- 查看场次的 KPI、商机快照、风险进展、CXO 报告。
- 按快照分组筛选，并重算预测汇总。
- 在场次内写洞察、对话，并触发索引构建。
- 查看单个商机详情。

未参加该场次的人不能把这场复盘当作自己的会话。未参加的复盘通知仍按出席人实例路由，没有个人 opt-out。

## 5. 周跟进

模块：`crm/weekly_followup`。

- 查询周跟进汇总和明细，导出明细，读取筛选项。
- 对汇总标记已阅，对实体写评论。
- 可手动触发生成周跟进，以及负责人参与度报告。

周拜访报告 Markdown 作为公司/部门周报的 `visit_report` 变体推送，资格与同一槽位周报相同。

## 6. 日周报与通知

模块：`reports`、`notification`。

报告查询：

- 个人、部门、公司日报。
- 部门、公司周报。部门、公司周报正文从 Aldebaran 拉取。未指定部门时，先取有负责人的部门。
- 日报统计在拿到执行结果后可以读销售四象限，作为日报内容的一部分。没有单独的四象限查询接口。

推送资格：

- 个人日报只发给本人，本人可关掉。
- 部门日周报发给该部门负责人和团队接收人，群按部门群配置发送。负责人只能关自己负责的部门。
- 公司日周报发给持有对应公司接收权限的人。
- 日拜访、周拜访的 Markdown 变体可单独指定接收人。未指定时与对应定时任务同一套收件人。
- 实际个人收件人 = 资格名单 − 已关掉该场景的人。无资格的人改偏好返回 403。

用户接口：

- `GET /notification/scenes`、`GET /notification/preview` 分别看场景目录和「会发给谁」。这两项是功能权限，不是接收资格。
- `GET/PUT /notification/preferences` 只处理自己有资格的项。
- `POST /notification/push` 按类型补发已有报告。

拜访卡和复盘会通知不走这层偏好。

## 7. 客户、文档与 CRM 视图

- `crm/views`：按字段和筛选项分页查看客户视图。
- `crm/config`：读取 CRM 字段映射。
- `crm/documents`：上传客户文档、按客户列出和打开文档，并触发文档问答。
- `contacts`：维护本地联系人，并按客户查询。
- 上传与对象存储：申请 STS、登记上传、预览并确认文档与来源文件的匹配。
- 下载知识库文档：`GET /documents/{doc_id}/download`。`kind=processed` 时下载已索引文件，否则下载原文件。
- 客户、商机等范围查询使用 `crm_data_authority`。这张表由其他系统维护，本服务只读。本地联系人不在这张表上授权，只校验所属客户。

超级管理员可以手动触发这些任务，并用 `GET /crm/task-status/{task_id}` 看 Celery 状态：

- 日报统计，以及向指定部门补发日报。
- 周报。
- 按时间窗口或按拜访 id 把拜访回写到外部 CRM。未配置默认回写模式时，定时回写不注册，手动任务直接返回且不查库。
- 销售任务汇总。未传日期时，窗口是上周日到本周六。
- 多维表格回写，可按记录 id 指定范围。
- 重建拜访指标、重建待办指标、写入待办小时库存。

对应定时任务在各自开关打开后运行：多维表格同步、日报统计、周报、周跟进摘要（默认周日的部门与公司）、负责人参与度、拜访回写、销售任务汇总、拜访指标、待办指标、待办小时库存。日报数据源另有 `CRM_DAILY_TASK_ENABLED`。具体时刻见技术说明书。

## 8. 知识库与对话

- 管理员创建和维护知识库、数据源、文档、分块，查看索引进度与失败任务，并重建向量或图谱索引。
- 图谱可检索、查看子图和整图。
- 对话用户创建会话、发消息、读取推荐问题。可用的 Chat Engine 由管理员配置。
- `POST /retrieve/chunks` 与 `POST /retrieve/knowledge_graph` 提供检索。
- 管理员配置 LLM、Embedding、Reranker，并指定默认模型。
- 评测：维护数据集和评测任务，查看任务摘要和明细。
- 用户可提交反馈。管理员查看反馈、对话来源统计、语义缓存、Langfuse 连通性和站点设置。
- 知识库索引分普通图谱、Playbook 图谱和 CRM 图谱，可以分别重建。

## 9. 计费

`CRM_BILLING_ENABLED` 打开后，拜访、对话、复盘对话、个人/部门/公司日报、团队周报和周跟进摘要会查飞书 AI 额度并上报用量。

开通按这个顺序判断：租户是否使用计费包；该 SKU 在 `apt_sell_billing` 是否启用（缺行或读表失败视为开通）；欠费是否放行由 `CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA` 决定。查额度失败时记错误并视为额度充足。

## 10. 验收

对照 `1582acf1`：

- 拜访、复盘会、周跟进、日周报、客户文档、知识库和对话的接口与上面各节一致。
- 复盘对记录人、协同人、汇报链上级和其他打开详情的人按上一节选视角。
- 日周报按资格减掉个人关闭项后发送。
- 不存在今日重点查询接口、今日重点独立定时任务，也不存在按抽取结果发卡的行为。
- 不存在 `has_recap`，`CONCLUSION` 抽取仍回传 `status`。

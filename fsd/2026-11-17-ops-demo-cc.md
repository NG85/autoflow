# 功能规格说明书：运维抄送扩展与演示旁路抄送

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-17 |
| 变更窗口 | 2026-11-17 |
| 前置说明书 | `fsd/2026-11-11-wecom-bot-push.md`、`fsd/2026-11-12-cxo-today-highlights.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉；企微经 Sia bot（运维/演示可配独立 Sia 根地址） |
| 关联功能模块 | notification、platform_notification_service、今日重点、例会复盘、报告 Markdown |
| 前端接入 | 无页面契约变更 |

本文件约定两条互不干扰的旁路抄送：产品运维抄送（`OPS_CC_*`）与演示环境旁路（`DEMO_CC_*`）。历史说明书不改。拜访记录主卡仍走 `department_group_chats`，不进入 `DEMO_CC`。

## 1. 两条通道

| 通道 | 用途 | 默认 |
| --- | --- | --- |
| `OPS_CC_*` | 产品通用运维后门，把指定 scene 抄到运维群/人 | `OPS_CC_PROVIDER=off` |
| `DEMO_CC_*` | 仅演示环境镜像推送，便于在指定群观察全量卡片 | `DEMO_CC_PROVIDER=off` |

- 两套配置完全独立：各自的 `PROVIDER` + 通用凭证/目标（`APP_ID` / `APP_SECRET` / `USER_IDS` / `CHAT_IDS` / `BASE_URL` / `TOKEN`）。字段语义随 `PROVIDER` 解释。
- 旁路失败不影响主推送，不计费。
- 企微旁路必须配置该通道自己的 `*_BASE_URL`；**不**回落当前环境的 `WECOM_PUSH_*`。

## 2. 运维抄送（OPS_CC）

### 2.1 白名单

由 `OPS_CC_SCENES` 配置。条目为 `scene` 或 `scene.variant`（大小写不敏感）。

- 配 `scene`：该 scene 下所有 variant 放行。
- 配 `scene.variant`：仅精确匹配。
- 未配置时默认：`company_daily`、`company_weekly`、`company_highlights.today_highlights`（与历史硬编码一致）。

### 2.2 覆盖行为

在白名单内且 provider 开启时，下列发出成功路径各抄一次（best-effort）：

1. 公司日/周报 KPI 卡  
2. 公司日拜访报告 Markdown（`summary_md`）、公司周拜访报告 Markdown（`visit_report`）；无正文占位说明不抄  
3. CXO 今日重点（整次推送只抄一次，含企微 template_card）

部门日/周报、销售个人日报、销售/团队今日重点、例会复盘、评论族、拜访卡：**默认不进 OPS_CC**（除非日后把对应 scene 写进 `OPS_CC_SCENES` 且代码入口传入 scene；当前部门与销售入口不传 OPS scene）。

## 3. 演示旁路（DEMO_CC）

### 3.1 开关与过滤

- `DEMO_CC_PROVIDER`：`off` | `feishu` | `dingtalk` | `wecom`。
- `DEMO_CC_EVENTS`：空或含 `all` → 命中旁路的事件全抄；否则按 **source 名**精确匹配（大小写不敏感）。

### 3.2 接近全量覆盖（不含拜访卡）

开启后，下列推送有旁路镜像到演示群/人：

| 类别 | source 示例 |
| --- | --- |
| 报告 KPI | `sales daily report`、`department daily report`、`company daily report`、周报同理 |
| 销售任务卡 | `sales task` |
| 报告 Markdown | `company daily report`、`department daily report`、`company weekly report`、`department weekly report`（有正文时；占位无记录不抄） |
| 今日重点 | `company today highlights`、`sales today highlights`、`department today highlights` |
| 例会复盘卡 | `leader meeting review card` |
| 评论族 | `comment notification`（周跟进/拜访评论/今日重点评论/例会评论/任务创建/无跟进/review session 共用） |
| 短文本 | `send_text_notification_to_recipients` 的 `notification_type`（如空日报短文本） |

**拜访记录主卡**（个人上级卡、review 群、brief 群）不调用演示旁路。演示环境要观察拜访卡时，在站点配置 `department_group_chats` 把能收拜访的群指到演示群即可。

### 3.3 部门报告与群

部门日/周报 KPI：若已配置 `department_review` 类群，主推只打群、不额外走个人通道上的 DEMO_CC 入口。此时指定群靠 `department_group_chats`；Markdown 路径在有正文时仍会按 `demo_cc_source` 旁路一次。

## 4. 验收

- `OPS_CC_PROVIDER` 与 `DEMO_CC_PROVIDER` 可分别开关；改一侧不影响另一侧目标群。
- 开启 OPS 且场景在白名单内时，公司日报 KPI / 公司 Markdown / CXO 今日重点各至多抄一次到 OPS 目标。
- 开启 DEMO 且未限制 `DEMO_CC_EVENTS` 时，上表非拜访推送可镜像到 DEMO 目标；拜访主卡仍只出现在 `department_group_chats`（若已配）。
- 企微 OPS/DEMO 未配专用 `OPS_CC_BASE_URL` / `DEMO_CC_BASE_URL` 时该旁路跳过，不误用租户 `WECOM_PUSH_BASE_URL`。
- 旁路失败不改变主推送成功/失败结果，不产生计费上报。

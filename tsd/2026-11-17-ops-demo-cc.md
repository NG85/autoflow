# 技术规格说明书：运维抄送扩展与演示旁路抄送

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-17 |
| 变更窗口 | 2026-11-17 |
| 前置说明书 | `tsd/2026-11-11-wecom-bot-push.md` |
| 对应 FSD | `fsd/2026-11-17-ops-demo-cc.md` |
| 关联服务 | backend；飞书 / 钉钉；企微 Sia webhook |
| 关联功能模块 | `platform_notification_service`、`report_markdown_dispatch`、今日重点、例会复盘、`platforms/wecom/push_client` |

## 1. 配置（`app/core/config.py`）

### OPS_CC

| 配置 | 含义 |
| --- | --- |
| `OPS_CC_PROVIDER` | `off` / `feishu` / `dingtalk` / `wecom` |
| `OPS_CC_SCENES` | scene 或 `scene.variant` 列表；空则默认三元组 |
| `OPS_CC_APP_ID` / `OPS_CC_APP_SECRET` | 飞书/钉钉应用凭证；企微不用 |
| `OPS_CC_USER_IDS` | 飞书=open_id；钉钉/企微=userid |
| `OPS_CC_CHAT_IDS` | 群 id（企微为 Sia chatid） |
| `OPS_CC_BASE_URL` / `OPS_CC_TOKEN` | 仅企微：专用 Sia；**不**回落 `WECOM_PUSH_*` |

默认场景常量：`OPS_CC_DEFAULT_SCENES = (company_daily, company_weekly, company_highlights.today_highlights)`。

读取时把通用项映射进 `_cc_platform_card` 的飞书/钉钉/企微参数；实际只消费当前 `PROVIDER` 对应字段。

### DEMO_CC

| 配置 | 含义 |
| --- | --- |
| `DEMO_CC_PROVIDER` | 同上 |
| `DEMO_CC_EVENTS` | source 白名单；空/`all` = 旁路入口全放行 |
| `DEMO_CC_APP_ID` / `DEMO_CC_APP_SECRET` / `DEMO_CC_USER_IDS` / `DEMO_CC_CHAT_IDS` | 与 OPS 对称 |
| `DEMO_CC_BASE_URL` / `DEMO_CC_TOKEN` | 仅企微：演示专用 Sia |

拜访演示不配 `DEMO_CC`，配站点 `department_group_chats`。

## 2. 核心 API

文件：`platform_notification_service.py`。

| 符号 | 职责 |
| --- | --- |
| `_parse_cc_provider` / `_parse_ops_cc_provider` / `_parse_demo_cc_provider` | provider → 平台常量或 disabled/invalid |
| `ops_cc_scene_allowed(scene, variant)` | 解析 `OPS_CC_SCENES` |
| `_demo_cc_event_allowed(source)` | 解析 `DEMO_CC_EVENTS` |
| `_cc_platform_card(...)` | 共享发送：按目标平台打 open_id / chat_id；企微传显式 `base_url` |
| `_ops_cc_platform_card` | 运维封装 + scene 过滤 |
| `_demo_cc_platform_card` | 演示封装 + source 过滤 |
| `mirror_demo_cc(...)` | 演示统一入口：按 provider 选 feishu_card / wecom_card / markdown_text |
| `_build_cc_card_content_for_platform` | KPI 模板 → 飞书 interactive / 企微精简卡 |

`WeComPushClient._resolve_base_url`：`base_url is not None` 时只用传入值（可为空→未配置），否则读 `WECOM_PUSH_BASE_URL`。

## 3. 挂载点

| 路径 | OPS | DEMO |
| --- | --- | --- |
| `_send_notifications_by_platform` | 调用方传入 `ops_cc_scene`/`variant` 时 | 始终尝试（provider≠off） |
| `send_company_daily/weekly_report_notification` | 传入 company scene | 经上者 |
| `send_sales_*` / `send_department_*`（个人通道） | 不传 scene | 经上者 |
| `_send_report_to_department_review_groups_or_recipients` 有群早退 | 无 | **无**（靠群主推） |
| `report_markdown_dispatch.send_markdown_messages` | `ops_cc_scene` + 白名单 | `demo_cc_source` |
| `daily_summary_push` / `report_ready_push` | 公司传 scene；部门传 scene 但默认白名单不含部门 | 公司/部门均传 source；占位正文不传 |
| `company_today_highlights` | `_ops_cc_company_today_highlights` | `_demo_cc_company_today_highlights` → `mirror_demo_cc` |
| `sales_today_highlights` / `department_today_highlights` | 无 | 每张有效卡 `mirror_demo_cc` |
| `leader_meeting_review_card` | 无 | 整次一次 `mirror_demo_cc` |
| `send_text_notification_to_recipients` | 无 | `mirror_demo_cc(source=notification_type)` |
| `send_weekly_followup_comment_notification`（评论族） | 无 | `mirror_demo_cc(source=comment notification)` |
| `send_visit_record_notification` | 无 | **无**（群走 `department_group_chats`） |
| `send_platform_notification` 通用 API | 无 | 无（业务入口自行 mirror） |

## 4. Source / scene 约定

DEMO `source` 为现有 `notification_type` 字符串或业务自拟名（见 FSD 表），供 `DEMO_CC_EVENTS` 精确匹配。

OPS 日志源：`{scene}` 或 `{scene}.{variant}`。

## 5. 测试

- `tests/test_demo_cc.py`：provider 解析、事件白名单、OPS/DEMO 隔离、企微无专用 BASE_URL 跳过、`mirror_demo_cc`、评论/短文本/CXO 挂载。
- `tests/test_ops_cc_scenes.py`：`OPS_CC_SCENES` 解析与 `_send_notifications_by_platform` 传 scene。
- `tests/test_daily_summary_push.py` / `tests/test_report_ready_push.py`：Markdown 路径 Demo CC。
- `tests/test_wecom_push_client.py`：`base_url` 显式传入不回落。

## 6. 运维提示

演示环境最小配置：

1. `DEMO_CC_PROVIDER` + `DEMO_CC_CHAT_IDS`/`USER_IDS` +（飞书/钉钉）`APP_ID`/`APP_SECRET` +（企微）`DEMO_CC_BASE_URL`
2. 拜访：`department_group_chats` 中 visit 相关 `notification_type` 指向演示群

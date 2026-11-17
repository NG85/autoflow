# 技术规格说明书：企微经 Sia bot 推送消息

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-11 |
| 变更窗口 | 2026-11-11 |
| 前置说明书 | 无 |
| 对应 FSD | `fsd/2026-11-11-wecom-bot-push.md` |
| 关联服务 | backend；Sia webhook |
| 关联功能模块 | `platforms/wecom`、`platform_notification_service`、今日重点 / 拜访 lite / 例会复盘 / `/notification/push` |

## 1. 配置

| 配置 | 含义 |
| --- | --- |
| `WECOM_PUSH_BASE_URL` | Sia 服务根地址（空=不推；有即可推） |
| `WECOM_PUSH_TOKEN` | 可选 Bearer；对应 Sia `SYNAPSE_TASK_CALLBACK_TOKEN`。Sia 未配该 token 时 `verify_callback_token` 直接放行，本侧空则不带 `Authorization` |

触达 `provider=wecom` 个人账号或 `department_group_chats` 企微群时走此通道。部门日/周报群发策略与渠道无关：**有 review 群则只打群，无群才推个人**（飞书/钉钉/企微同一规则）。OAuth 权限 API 直接读 `openId`（企微由 OAuth 服务用 userid 回填）；本地 `user_oauth_accounts` 仍可对空 open_id 回退 `user_id_in_platform`。部门/公司 KPI 精简卡跳转报告页；销售个人日报 KPI 等其余飞书模板仍跳过。拜访 lite/legacy 同前。

请求：`POST {BASE}/api/v1/webhook/wecom/push`；业务卡 body 为 `{chatid, msgtype: template_card, template_card: {...}}`，通用通知可为 markdown。

## 2. 代码落点

- `PLATFORM_WECOM` 加入 `SUPPORTED_PLATFORMS`；`visit_record_cc_resolver` 白名单同步。
- `app/platforms/wecom/push_client.py`：HTTP client，支持 markdown 与 `template_card`，无 WS。
- `app/platforms/wecom/text_notice.py`：共用 `text_notice` / `build_link_notice_card`。
- `oauth_push_receive_id`（本地档案）：企微 `open_id` 空时回退 `user_id_in_platform` / `union_id`；OAuth API 层不再做该回退。
- `today_highlights_wecom.py`：销售 / 团队卡。
- `visit_record_recap_card` / `leader_meeting_review_card`：lite / 例会 → template_card。
- `send_weekly_followup_comment_notification`：评论族 / review / 任务创建 / 无跟进 → template_card（飞书 interactive，钉钉 markdown）。
- `send_platform_notification`：企微有链或 markdown → template_card。

### 业务卡链接组织

| 槽位 | 规则 |
| --- | --- |
| `jump_list` | 主入口（查看/更多），≤3 |
| `horizontal_content_list` | 次要入口，`{type:1, keyname, value, url}`，≤6；key≠value |
| `card_action` | 整卡点击，默认与首条 jump 同链 |

| 场景 | jump | horizontal（示例 key / value） |
| --- | --- | --- |
| 今日重点 | 查看/更多（团队空态→`department-highlights?date=`） | 关键发现/2条；本周任务/待完成列表；本周拜访/已录入记录 |
| 拜访 lite | 完整复盘 + 跟进详情 | 抽取入口 |
| 拜访 legacy（企微） | 跟进详情 | 无（仅基础信息正文） |
| 部门/公司日周报 KPI | 查看报告 | 无（部门/日期或周期） |
| 例会复盘 | 完整复盘 | 待办/N条；经营/查看事项 |
| 评论 / 任务 | 首条原标签 | 其余入口（自动拆 key/value） |

## 3. 测试

`test_wecom_push_client.py`、`test_today_highlights_wecom.py`、`test_visit_record_recap_card.py`、`test_leader_meeting_review_card_push.py`、`test_comment_notification_applink.py`、`test_platform_notification_push.py`。

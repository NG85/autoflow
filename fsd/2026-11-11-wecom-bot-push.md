# 功能规格说明书：企微经 Sia bot 推送消息

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-11 |
| 变更窗口 | 2026-11-11 |
| 前置说明书 | 无（新通道） |
| 关联服务 | backend；Sia（`/api/v1/webhook/wecom/push`）；企微智能机器人 |
| 关联功能模块 | notification、platforms/wecom、crm/today_highlights、visit_record 轻量卡 |
| 前端接入 | 无页面契约变更；推送外观为 Sia bot |

本文件约定本服务向企微发 bot 消息的通道与范围。历史说明书不改。

## 1. 通道

- 只发 **Sia 智能机器人 bot 消息**，不发企微自建应用通知。
- 本服务 **不** 建企微 Bot 长连接，**不** 使用 `WECHAT_BOT_ID` / `WECHAT_BOT_SECRET`。
- 统一调用 Sia 已封装接口转发；会话外观为 Sia。
- 个人收件人：`provider=wecom`；OAuth API 的 `openId` 由 OAuth 服务用 userid 回填，本侧 API 层直接读。本地档案 `chatid` 优先 `open_id`，仍空则回退 `user_id_in_platform`（其次 `union_id`）。
- 群：`department_group_chats` 中 `platform=wecom` 的 `chat_id` 直接作 `chatid`（与个人同一 push 接口）。
- **带链接的消息**一律 `msgtype=template_card`（`text_notice`）：正文纯文本；主入口进 `jump_list`（≤3）；次要入口进 `horizontal_content_list` type=1（左灰短标签 + 右蓝说明，避免 key/value 同文，≤6）；`card_action` 整卡进主入口。覆盖今日重点、拜访 lite、例会复盘、评论族、review session、销售任务创建、当日无跟进提醒，以及通用 `platform_notification` 含超链/markdown 的推送。不做 `button_interaction` 回调。
- 企微纯文本（无超链且非 markdown）仍可走 bot markdown。

## 2. 覆盖场景

1. 拜访复盘轻量卡（`template_card`）  
2. 销售今日重点卡（`template_card`）  
3. 团队今日重点卡（`template_card`）  
4. 例会复盘卡（`template_card`）  
5. 评论 / review session / 销售任务创建 / 当日无跟进提醒（`template_card`）  
6. 通用 `platform_notification`（有链或 markdown → `template_card`）  
7. 部门/公司日周报 KPI：企微精简卡（标题 + 部门/日期或周期 +「查看报告」）；群发策略与渠道无关——有 review 群则只打群，无群才推个人（飞书/钉钉/企微同一规则）；销售个人日报 KPI 等其余飞书模板仍跳过  


8. 拜访个人/群企微：`recap_lite` 发完整 lite；`legacy` 发精简卡（基础信息 + 跟进详情）；简报群可发文本/lite  

## 3. 验收

- 配了 Sia BASE_URL 时，上述带链消息向 wecom 发 `template_card`；jump_list ≤3；回调 token 可选。
- 缺 BASE_URL 时，企微收件人不可达 / 明确失败，不影响飞书、钉钉。
- 不出现对本服务进程的企微 Bot WebSocket 建连。

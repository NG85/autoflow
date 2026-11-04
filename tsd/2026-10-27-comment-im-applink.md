# 技术规格说明书：评论推送链接走 IM 侧栏

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-27 |
| 变更窗口 | 2026-10-27 |
| 前置说明书 | `tsd/2026-10-23-comment-edit.md` |
| 关联服务 | backend |
| 关联功能模块 | notification、crm/visit_records、crm/weekly_followup |

本文件描述评论提醒链接改写的 backend 实现。用户可见行为见 `fsd/2026-10-27-comment-im-applink.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 评论提醒发送、按平台改写链接 | `send_weekly_followup_comment_notification` |
| 拜访评论复用同一发送 | `send_visit_record_comment_notification` |
| 侧栏 / 窗口协议 | `backend/app/utils/im_applink.py` |
| 单测 | `backend/tests/test_comment_notification_applink.py` |

## 2. 发送形态

`_layout_comment_style_message` 把首行当标题，整行 markdown 链接拆成按钮，`评论：` / `回复：` 改成引用。然后对正文和按钮 URL 做 `rewrite_im_content_urls`。

- 飞书 / Lark：`build_feishu_markdown_card`，引用在正文，链接在 `footer_buttons`，编辑过用橙色标题，`msg_type=interactive`。
- 钉钉：标题 + 引用 + markdown 链接，`msg_type=text`（底层 `sampleMarkdown`）。

拜访详情、周跟进详情默认 `open_mode=sidebar`。已经是 applink 或外链的地址不重复包装。

## 3. 测试

- 飞书拜访评论卡片正文 href 为 `applink.feishu.cn` 且 `mode=sidebar-semi`。
- Lark 周跟进评论走 `applink.larksuite.com`。
- 钉钉评论文本为 `dingtalk://...&pc_slide=true`，解码后仍是原页面 URL。

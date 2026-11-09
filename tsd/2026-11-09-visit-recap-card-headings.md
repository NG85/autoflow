# 技术规格说明书：复盘卡里的业务推进和销售表现是小标题

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-09 |
| 变更窗口 | 2026-11-09 |
| 前置说明书 | `tsd/2026-11-09-visit-recap-performance.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | notification、visit_record |

本文件描述轻量卡摘要的标题语法。用户可见行为见 `fsd/2026-11-09-visit-recap-card-headings.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 按渠道拼摘要 | `backend/app/services/visit_record_recap_card.py` 的 `compose_recap_card_summary` |
| 飞书、钉钉各用一段摘要 | 同文件 `build_recap_lite_card` |
| 单测 | `backend/tests/test_visit_record_recap_card.py`、`test_visit_record_mixed_card_send.py` |

## 2. 语法

飞书卡片 JSON 2.0 用四级标题 `####`，标题和正文之间空一行。

钉钉走 sampleMarkdown。四级标题不会放大，普通空行还会被折叠。钉钉因此用二级标题 `##`，两段之间插入不断行空格 `\u00a0`，与例会复盘卡、今日重点卡同一做法。

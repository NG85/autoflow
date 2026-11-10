# 技术规格说明书：销售今日重点把建议关注改成关键发现

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-10 |
| 变更窗口 | 2026-11-10 |
| 前置说明书 | `tsd/2026-11-09-today-highlights-separate-cards.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |

用户可见行为见 `fsd/2026-11-10-sales-highlights-key-findings.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 展示名替换 | `backend/app/services/sales_today_highlights.py` 的 `_SECTION_TITLE_RENAMES` |
| 卡片蓝链与页面 tab | 同文件 `assemble_sales_highlights` 写出的 `sections[].title`，经 `card_blue_links` 和 `highlights_payload` 出去 |
| 单测 | `backend/tests/test_sales_today_highlights.py` |

## 2. 规则

`open_risks` 的快照标题「商机风险」「建议关注」都替换成「关键发现」。已经是「关键发现」或其它标题的不改。替换发生在组装销售今日重点时，不回写抽取行。

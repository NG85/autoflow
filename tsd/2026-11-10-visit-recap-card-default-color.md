# 技术规格说明书：拜访复盘卡片使用默认颜色

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-10 |
| 变更窗口 | 2026-11-10 |
| 前置说明书 | `tsd/2026-11-09-visit-recap-card-headings.md` |
| 关联服务 | backend；飞书 / Lark |
| 关联功能模块 | notification、visit_record |

用户可见行为见 `fsd/2026-11-10-visit-recap-card-default-color.md`。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 质量词与卡片头颜色 | `backend/app/services/visit_record_recap_card.py` 的 `resolve_recap_quality` |
| 飞书卡片头 | `RecapLiteCard.header_template`，由 `build_feishu_markdown_card` 写入 `header.template` |
| 单测 | `backend/tests/test_visit_record_recap_card.py` |

## 2. 规则

`severity` 仍只映射标题质量词：`LOW` → 正常，`MEDIUM` → 留意，`HIGH` → 需关注。`header_template` 固定为 `blue`，与飞书卡片头缺省色板一致。

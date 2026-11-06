# 功能规格说明书：周一今日重点不放日报

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-04 |
| 变更窗口 | 2026-11-04 |
| 前置说明书 | `fsd/2026-11-04-today-highlights-one-card.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 |
| 关联功能模块 | crm/today_highlights、notification |
| 前端接入 | `backend/docs/workbench-today-highlights-frontend.md` |

本文件只改负责人今日重点卡片上的日报链接。历史说明书不改。其余发卡规则仍按 `fsd/2026-11-04-today-highlights-one-card.md`。

## 1. 这次做什么

周一的团队今日重点不放「日报」。周二到周日仍放前一天的「日报」。

## 2. 链接

位置仍在团队今日重点这一块里，和报告入口同一行，排在报告入口后面、本周按钮前面。不进「本周」按钮，也不单独触发发卡。没有站点地址时不放。

```text
/v2/business/reports?tab=daily-team&date=YYYY-MM-DD
```

`date` 是今日重点业务日的前一天。卡片标题仍用当天。

## 3. 验收

- 业务日是周一时，卡片没有「日报」。
- 业务日不是周一时，卡片有「日报」，日期是前一天。
- 本周按钮不变。

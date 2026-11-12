# 功能规格说明书：CXO 今日重点

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-12 |
| 变更窗口 | 2026-11-12 |
| 前置说明书 | `fsd/2026-10-19-release.md`、`fsd/2026-10-27-leader-insight-reports.md`、`fsd/2026-10-27-today-highlights-billing.md` |
| 关联服务 | backend；飞书 / Lark / 钉钉 / 企微 Sia bot |
| 关联功能模块 | crm/today_highlights、notification |
| 前端接入 | `backend/docs/workbench-today-highlights-frontend.md` |

本文件把公司今日重点的发送路径接上。历史说明书不改。销售个人卡和团队卡不在这次范围内。

## 1. 这次做什么

调度到点后给 CXO 发一张卡，标题是「CXO今日重点｜日期」。卡片展示两份只读报告的入口：公司人效、重点商机盘点。有哪份才出现哪份。

收件人优先来自 `company_highlights.today_highlights` 的 `recipient_user_ids`（本地 user_id）。这份名单没配时，改用公司日报接收人（OAuth `notification:daily_report_company:receive`）。不进群，不抄送。全体接收人收到同一张卡。

## 2. 什么时候发

公司卡单独调度：`CRM_COMPANY_TODAY_HIGHLIGHTS_ENABLED` 控制是否挂上 beat，`CRM_COMPANY_TODAY_HIGHLIGHTS_CRON` 控制时刻，默认周一、三、五 08:00。团队卡和一线卡仍走 `CRM_TODAY_HIGHLIGHTS_CRON`。`POST /notification/push` 的 `type=company_today_highlights` 走同一处理，不按星期拒绝。手动触发不传 audience 时三边都推。

变体关闭、额度或 SKU 拦住、两份报告都没有、没有可推送的人、本人关掉，都不发空卡。HTTP 仍 200。

可选 `recipient_user_ids` 只能从这一批收件人里收窄，不能把名单以外的人加进来。

## 3. 卡片和页面

飞书、Lark、钉钉正文是一行蓝链，多条之间用「｜」分隔。企微走 `template_card`，两份报告都在 `jump_list`，整卡打开第一份。

链接打开 `/v2/company-highlights?date=YYYY-MM-DD&tab=`。`date` 是推送日。

| tab | 文案 | `insight_type` |
| --- | --- | --- |
| `cxo_efficiency_insight` | 公司人效 | `CXO_EFFICIENCY_INSIGHT` |
| `cxo_key_deal_review` | 重点商机盘点 | `CXO_KEY_DEAL_REVIEW` |

`GET /crm/today-highlights/company` 给两类当前用户返回正文：角色是 `biz_admin`、`sys_admin`、`executive` 的人，以及这次的推送接收人。其他人得到空 `items`。同一 tab 可以有多条，按部门分；公司级 `entity_id=__COMPANY__` 排在最前，名称写「公司」。条目只读：`unique_id`、`summary`、`detail_text`、`department_id`、`department_name`。

## 4. 数据口径

读 `crm_entity_insight`。`entity_type=DEPARTMENT`。`period_key` 等于推送日，也就是业务日，和其他今日重点一样。每个部门、每种洞察取未删除、未过期的最新一条。摘要和全文都空则这条不出现。

重点商机盘点只在周一、周五写出。周三卡片可以只有公司人效。

## 5. 计费

不新开 SKU。发卡成功后按人记到团队日报计费点，用量键前缀 `cxo-today`，和部门日报、团队今日重点错开。没发出的不报。

## 6. 验收

- 周一同时有两份洞察时，推送接收人收到一张卡，蓝链顺序是公司人效、重点商机盘点。名单没配时，接收人是公司日报接收人。
- 周三没有重点商机盘点时，卡片不占这个入口。
- 周二调用推送接口时，不因星期被拒绝。
- `biz_admin`、`sys_admin`、`executive` 打得开报告正文。推送接收人打得开。其他人打不开。
- 变体关闭、关掉、没有 open_id、SKU 关闭或额度不足时 skip。
- 销售个人卡和团队卡不受影响。团队日报 SKU 关闭时，这张卡也不发。

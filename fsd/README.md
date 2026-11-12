# 功能规格说明书（FSD）

本目录存放 Autoflow backend 的功能规格说明书。说明书由 AI 根据代码或需求生成，由本仓库负责人维护。

## 必要要素

每份文档开头写明：

| 要素 | 内容 |
| --- | --- |
| 作者 | 负责该功能的研发 |
| 时间 | 文档生成日期 |
| 关联服务 | 本仓库的 `backend`、`core`，以及这次改动依赖的外部服务 |
| 关联功能模块 | 对应代码模块，例如 `crm/visit_records` |

功能规格写行为、角色、输入输出和验收。实现落点写在 `tsd/`。

## 怎么追加

已提交的说明书不再修改。功能增加或迭代时，新增一份 FSD，在新文档里指向上一份。

文件名带日期和范围，例如 `2026-10-19-release.md`。

新功能发版时，当次上线的 change list 里至少包含一份新的 FSD。

## 当前文档

| 文件 | 范围 |
| --- | --- |
| `2026-10-10-autoflow-baseline.md` | `1582acf1`（2026-10-10）及之前 |
| `2026-10-19-release.md` | 2026-10-12 至 2026-10-19 |
| `2026-10-23-comment-edit.md` | 2026-10-23，拜访记录与周跟进评论可编辑 |
| `2026-10-27-leader-insight-reports.md` | 2026-10-27，负责人今日重点三个报告入口 |
| `2026-10-27-today-highlights-billing.md` | 2026-10-27，今日重点按对应日报计费点上报 |
| `2026-10-27-comment-im-applink.md` | 2026-10-27，评论推送链接走飞书 / 钉钉侧栏 |
| `2026-10-27-comment-notify.md` | 2026-10-27，评论提醒可指定接收人（含今日需介入） |
| `2026-10-29-leader-meeting-review-card.md` | 2026-10-29，例会复盘抽取卡片发给上传人 |
| `2026-10-30-comment-card.md` | 2026-10-30，评论卡片文案、回复原文与作者名 |
| `2026-10-30-leader-meeting-review-extract-read.md` | 2026-10-30，例会复盘抽取登录即可查看 |
| `2026-11-04-today-highlights-one-card.md` | 2026-11-04，今日重点一人一天一张卡 |
| `2026-11-04-today-highlights-monday-omits-daily-report.md` | 2026-11-04，周一今日重点不放日报 |
| `2026-11-05-leader-meeting-review-two-recipients.md` | 2026-11-05，例会复盘卡片发给负责人和提交人 |
| `2026-11-06-today-highlights-monday-weekly-report.md` | 2026-11-06，周一今日重点放上周周报 |
| `2026-11-06-leader-meeting-review-todo-candidates.md` | 2026-11-06，例会复盘卡片正文是会议总结和完整复盘报告 |
| `2026-11-09-visit-review-group-replaces.md` | 2026-11-09，拜访群可以不接替 leader |
| `2026-11-09-visit-recap-performance.md` | 2026-11-09，拜访复盘增加人员表现评价 |
| `2026-11-09-visit-collaborator-leader.md` | 2026-11-09，协同人与 leader 重复时取 leader |
| `2026-11-09-visit-recap-card-headings.md` | 2026-11-09，复盘卡里的业务推进和销售表现是小标题 |
| `2026-11-09-team-highlights-team-prefix.md` | 2026-11-09，团队今日重点标题和蓝链加「团队」前缀 |
| `2026-11-09-today-highlights-separate-cards.md` | 2026-11-09，今日重点恢复销售个人与团队各自推卡 |
| `2026-11-10-team-highlights-friday-visit-watch.md` | 2026-11-10，周五团队拜访对照不放正文查看链 |
| `2026-11-10-visit-recap-card-default-color.md` | 2026-11-10，拜访复盘卡片头使用默认蓝色 |
| `2026-11-10-sales-highlights-key-findings.md` | 2026-11-10，销售今日重点把建议关注改成关键发现 |
| `2026-11-10-sales-immediate-status-null.md` | 2026-11-10，立即处理一对一绑定任务且不回传状态 |
| `2026-11-11-wecom-bot-push.md` | 2026-11-11，企微经 Sia bot 推送个人消息 |
| `2026-11-12-cxo-today-highlights.md` | 2026-11-12，CXO 今日重点推送 |
| `2026-11-12-visit-recap-audience.md` | 2026-11-12，拜访复盘谁能打开页面 |

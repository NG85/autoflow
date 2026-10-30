# 例会复盘卡片

## 背景

例会复盘报告在 Aldebaran 抽取成功后，本服务给上传人发一张卡片，并提供卡片上经营事项、员工待办的查询。列表和单份报告详情由 base-api 提供，本服务不读 `bi_reports`。页面对接见 `backend/docs/leader-meeting-review-frontend.md`。

## 今日重点入口

负责人今日重点卡片底部有一组「本周」按钮：

- 待完成：负责人本人、预计完成日在本周一到本周日、状态进行中或待处理。
- 已拜访：本周一到本周日，按部门名称筛选。周一不放。周五不再另写「查看本周拜访」。
- 例会复盘：周二到周日筛本周。周一不放。

链接只带 `start_date` 和 `end_date`，两端都包含，不必按周对齐，不带 `owner_id`。host 为空则不放例会复盘按钮。一线销售今日重点的「本周待完成」「本周已拜访」不变。

## 发卡

`POST /api/v1/notification/push`，`type=leader_meeting_review_card`。

- 必填 `report_id`、`owner_id`。可选 `report_date`。
- 只读该报告、该上传人的 `MANAGEMENT_EVENT` 与 `EMPLOYEE_TODO_CANDIDATE`。已删除或已被替代的不展示。
- 每类按置信度只展开最高的一条。还有其余条目时出口是「更多（N）」，只有一条时是「查看」。
- 两类都没有：不发，`reason=empty_extracts`。没有 open_id：`reason=no_open_id`。发送失败：`reason=send_failed`。
- 发给上传人本人，不进群，不计费。重复调用会再发一张。

## 抽取查询

`GET /api/v1/crm/leader-meeting-reviews/{report_id}/extracts`。

上传人本人，以及其汇报链上两级内的上级（本部门负责人，以及再上一级），可以查看和修改。公司级管理人员（经营层、业务管理员、系统管理员）可以查看，不能修改。其他人、没有身份或没有抽取时返回空 `items`。返回该报告的全部经营事项和员工待办，不只是卡片上展开的那一条。查询用 `can_manage` 标出当前用户能不能改。赞踩和采纳记在条目上，后一次覆盖前一次。

经营事项和员工待办都可以赞踩、评论和回复。评论只能改自己写的正文。员工待办还可以采纳、拒绝，以及把已经建好的任务挂回来。

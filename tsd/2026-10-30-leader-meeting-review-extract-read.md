# 技术规格说明书：例会复盘抽取可查看

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-10-30 |
| 变更窗口 | 2026-10-30 |
| 前置说明书 | `tsd/2026-10-29-leader-meeting-review-card.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/leader_meeting_reviews |

本文件描述抽取查询不再按身份过滤。用户可见行为见 `fsd/2026-10-30-leader-meeting-review-extract-read.md`。历史技术说明书不改。没有新增表或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 查询入口 | `GET /crm/leader-meeting-reviews/{report_id}/extracts` |
| 组装 | `extracts_for_viewer` |
| 可见行与 `can_manage` | `select_visible_rows` |
| 写入仍校验身份 | `load_meeting_review_item` |

`select_visible_rows` 的可见行是未删除、未作废的经营事项和员工待办。`can_manage` 仍要求当前用户能改每一条可见行，也就是上传人或汇报链上两级内的上级。公司级角色不再参与这次查询。

查询不再因为没有 oauth id 提前返回空。写入路径不改。

## 2. 测试

- `test_viewer_without_rows_gets_an_empty_payload`：他人和没有身份的人能看到抽取，`can_manage` 为 `false`；上传人为 `true`。
- `test_company_manager_can_view_but_not_change`：能看不能改，写入加载返回空。

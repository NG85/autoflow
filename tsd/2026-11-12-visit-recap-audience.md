# 技术规格说明书：拜访复盘谁能打开页面

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-12 |
| 变更窗口 | 2026-11-12 |
| 前置说明书 | `tsd/2026-11-09-visit-recap-performance.md`、`tsd/2026-11-09-visit-collaborator-leader.md` |
| 关联服务 | backend；OAuth |
| 关联功能模块 | crm/visit_records |

本文件描述复盘页的放行和视角选择。用户可见行为见 `fsd/2026-11-12-visit-recap-audience.md`。没有新增表或迁移。轻量卡发送路径不改。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 放行：能看拜访，或是个人收件人 | `backend/app/api/routes/crm/visit_records/router.py` 的 `get_visit_record_recap` |
| 身份与视角 | `backend/app/services/visit_record_recap.py` 的 `RecapViewContext`、`resolve_viewer_recap` |
| 列表 `has_recap` | `viewer_has_visible_recap`；`visit_record.py` 的 `_fill_has_recap` |
| 单测 | `backend/tests/test_visit_record_recap.py`、`test_visit_record_insight_reader.py` |

拜访是否可见仍走 `VisitRecordRepo._can_view_visit_record`。个人收件人即使这个判断为 false 也放行。

## 2. 身份

`RecapViewContext` 按记录人缓存。失败视为不是，不因此放行。

| 身份 | 判断 |
| --- | --- |
| 记录人 | 当前用户 id 与 `recorder_id` 相同，连字符有无都算同一人 |
| 直接汇报上级 | OAuth 汇报链 `max_levels=1` 的 `open_id`。链有结果时不用档案直属上级；链为空或查询失败时才退回 `direct_manager_id` |
| 公司级管理人员 | 角色码 `biz_admin`、`sys_admin`、`executive`，忽略大小写。查的是 `viewer_is_company_manager` |
| 协同人 | `collaborative_participants` 的 `ask_id` 对齐当前用户的 user id、oauth id 或 open_id |
| 抄送 | `resolve_visit_record_cc_recipients` 返回的 `open_id` 能对上当前用户 |

抄送只在当前用户是协同人，或看不到这条拜访时才查。记录人本人不查抄送，也不查汇报链。

看不到拜访、又不是记录人、协同人或抄送时，才查汇报链。对不上则 404。

## 3. 视角

`resolve_viewer_recap` 在四类洞察都没有时返回空，并且不查角色和汇报链。没有上级视角的人员表现评价时，也不为了评价去查角色和汇报链。

有报告时按这个顺序，命中即返回：

1. 记录人：销售视角的业务推进和人员表现评价。抽取在 `load_visit_record_recap_response` 里另判，只给记录人。
2. 上级视角的人员表现评价存在，并且当前用户是直接汇报上级或公司级管理人员：返回上级视角的两份。业务推进可以是 `null`。这一步不要求能看到拜访。
3. 协同人且不是抄送：有销售视角的业务推进或人员表现评价时返回销售视角。两份都没有，并且看不到拜访时返回空。能看到拜访时继续往下。
4. 有上级视角的业务推进：返回这一份，人员表现评价为 `null`。抄送、以及其他能看到拜访的人停在这里。

部门群不参与这次判断。

## 4. 列表

`_fill_has_recap` 只处理当前页已经通过拜访数据范围的记录。导出调用查询时 `include_has_recap=False`。

只有「没有上级视角业务推进、但有上级视角人员表现评价」时，才查公司级角色和汇报链。协同人再查一次抄送；抄送命中时不按协同人的销售视角计算 `has_recap`。

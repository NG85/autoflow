# 技术规格说明书：拜访记录相关文件资料

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-27 |
| 变更窗口 | 2026-11-27 |
| 前置说明书 | `tsd/2026-11-27-visit-recap-unified.md` |
| 对应 FSD | `fsd/2026-11-27-visit-related-files.md` |
| 关联服务 | backend |
| 关联功能模块 | crm/visit_records |

用户可见行为见对应 FSD。`attachment` 仍是单条图片附件。

## 1. 数据

`crm_sales_visit_records.related_files` 为可空 JSON 数组。元素字段：`name`、`url`。`url` 必填，按传入字符串原样入库，不检查是否为链接或对象存储 key，也不去掉首尾空白。空数组可以入库。迁移把已有行留空，查询响应的可空字段接受空值。

| 方言 | 迁移 |
| --- | --- |
| TiDB | `c8e1a4f7b203`，基于 `a8c1e4b7d902` |
| PostgreSQL | `d9f2b5c8e314`，基于 `b7e4a91c2d10` |

模型列在 `CRMSalesVisitRecord.related_files`。

## 2. 接口

创建请求 `VisitRecordBase.related_files`，简易版和完整版共用。查询响应 `VisitRecordResponse.related_files`。

`related_files` 由 `List[VisitRelatedFile]` 校验结构。不接受 JSON 字符串、单个对象、裸链接字符串，也不把这些形式包成数组。`url` 没有内容校验。查询读库内 JSON 数组，不再另做格式转换。

保存走现有 `save_visit_record_to_crm_table`：`model_dump` 后的数组按 JSON 写入，不进入 `attachment` 的字符串处理。

## 3. 导出和卡片

`VISIT_RECORD_EXPORT_COLUMNS` 不含 `related_files`。`build_export_row` 不写这个键。`VisitRecordQueryRequest.export_columns` 的可选列说明也不列它；请求该列时走未知列错误。

`fill_sales_visit_record_fields` 在写入字段映射前 `pop("related_files")`。创建推送和从库行重推都走这个函数，卡片模板变量里没有该字段。查询响应不经过这里。

## 4. 这次不改

- 修订接口仍只改跟进日期、跟进方式、跟进记录、下一步计划。
- 外部 CRM 回写仍只从 `attachment` 取签到地址和拍照时间。
- 图片附件的地点提取、多维表导入跳过附件，都不读 `related_files`。

## 5. 测试

`backend/tests/test_visit_related_files.py` 覆盖 `url` 原样保存、拒绝字符串和单个对象等非定义格式、与图片附件分离、卡片载荷去掉该字段、导出不含该列。

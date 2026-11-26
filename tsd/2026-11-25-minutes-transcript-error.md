# 技术规格说明书：妙记文字记录读取失败

| 要素 | 内容 |
| --- | --- |
| 作者 | gaona |
| 时间 | 2026-11-25 |
| 变更窗口 | 2026-11-25 |
| 前置说明书 | `tsd/2026-10-10-autoflow-baseline.md` |
| 对应 FSD | `fsd/2026-11-25-minutes-transcript-error.md` |
| 关联服务 | backend；飞书 / Lark |
| 关联功能模块 | document_processing、crm/visit_records、crm/documents |

用户可见行为见对应 FSD。没有新表、接口、权限或迁移。

## 1. 代码落点

| 职责 | 位置 |
| --- | --- |
| 失败文案 | `backend/app/services/document_processing_service.py` 的 `MINUTES_TRANSCRIPT_UNAVAILABLE_MESSAGE` |
| 何时使用 | 同文件 `_handle_platform_document`。`url_type == "minutes"` 且文字记录为空，或导出过程抛出未单独处理的异常时，返回这句。授权失败仍走 `generate_auth_response` |
| 文字记录请求 | `backend/app/platforms/base/lark_client.py` 的 `_get_minutes_content`。仍只调用 `GET /minutes/v1/minutes/{token}/transcript` |
| 调用方 | 跟进记录 `visit_records/router.py` 和客户文档 `documents/router.py` 继续原样返回 `message` |
| 单测 | `backend/tests/test_document_processing_service.py` |

## 2. 不改的路径

不调用妙记详情、`/artifacts`、纪要 `note_id` 或纪要文档。妙记授权范围仍是 `minutes:minutes:readonly` 和 `minutes:minutes.transcript:export`。

文档、知识库节点、钉钉听记、钉钉会议号和本地文件的读取与失败文案不变。

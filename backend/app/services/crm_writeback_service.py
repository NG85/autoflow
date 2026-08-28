"""CRM 网关回写。

职责边界（配置与实现一致）：
- **拜访记录回写**：``CRM_WRITEBACK_DEFAULT_MODE``（``None``=关闭）+ ``CrmVisitWritebackClient`` +
  ``CrmWritebackService`` 中以 ``writeback_visit_*`` / ``_execute_visit_writeback`` 为代表的路径。
  支持 CBG / APAC / OLM / CHAITIN / WEBEYE / FENBEITONG / LIEPIN / WYWJ / ZHIPU 等网关变体。
- **CRM review 商机回写**：``CRM_WRITEBACK_REVIEW_ENABLED`` + ``CrmReviewWritebackClient`` +
  ``writeback_review_opportunity_updates_to_crm``（成员修改草稿并提交等）；不读写 ``CRM_WRITEBACK_DEFAULT_MODE``。
"""

import httpx
import json
import logging
import re
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from datetime import date, datetime, time, timezone, timedelta
from sqlmodel import Session, select, text, or_
from app.core.config import settings, WritebackMode
from app.models.wb_review_requests import (
    ReviewOpportunityWritebackBatchRequest,
    ReviewOpportunityWritebackOp,
)
from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.models.task_requests import TaskCreateRequest, TaskBatchCreateRequest
from app.models.wb_visit_requests import (
    CbgVisitRecordBatchCreateRequest,
    CbgVisitRecordCreateRequest,
    CbgVisitRecordType,
    ChaitinVisitRecordBatchCreateRequest,
    ChaitinVisitRecordCreateRequest,
    FenbeitongVisitRecordBatchCreateRequest,
    FenbeitongVisitRecordCreateRequest,
    LiepinVisitContact,
    LiepinVisitRecordBatchCreateRequest,
    LiepinVisitRecordCreateRequest,
    OlmVisitRecordBatchCreateRequest,
    OlmVisitRecordCreateRequest,
    WebeyeVisitRecordBatchCreateRequest,
    WebeyeVisitRecordCreateRequest,
    WywjVisitRecordBatchCreateRequest,
    WywjVisitRecordCreateRequest,
    ZhipuVisitRecordBatchCreateRequest,
    ZhipuVisitRecordCreateRequest,
)
from app.models.crm_accounts import CRMAccount
from app.models.crm_leads import CRMLead
from app.api.routes.crm.models import VisitAttachment
from app.utils.crm_followup_object import (
    FOLLOWUP_OBJECT_TYPE_END_CUSTOMER,
    FOLLOWUP_OBJECT_TYPE_LEAD,
    FOLLOWUP_OBJECT_TYPE_PARTNER,
    FollowupObject,
    resolve_followup_object_from_record,
)

logger = logging.getLogger(__name__)


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _normalize_expected_sign_month(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw.isoformat()
    s = str(raw).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return f"{s}-01"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def _coerce_money(raw: Any) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, Decimal):
        return float(raw)
    return float(raw)


def _money_compare_value(raw: Any) -> Optional[float]:
    """用于判断金额是否变化；``None`` 表示空金额。"""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return round(float(raw), 6)
    return round(float(raw), 6)


def _coerce_reason_code(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if re.fullmatch(r"-?\d+", s):
            try:
                return int(s)
            except ValueError:
                return None
        return None
    return None


def _safe_parse_int_id(raw: Any) -> Optional[int]:
    """将 CRM 实体 ID（客户/伙伴等）安全转为 int；非数字或解析失败时返回 None。"""
    return _coerce_reason_code(raw)


def review_op_to_gateway_update_json(op: ReviewOpportunityWritebackOp) -> Dict[str, Any]:
    """仅包含相对变更前确有变化的可编辑字段 → 网关单条商机更新 JSON（camelCase，与 ``CrmBusinessOpportunityUpdateBody`` 对齐）。"""
    before = op.before_editable or {}
    after = op.after_editable or {}
    oid = str(op.opportunity_id or "").strip()
    payload: Dict[str, Any] = {"id": oid}

    if _str_or_none(before.get("opportunity_stage")) != _str_or_none(after.get("opportunity_stage")):
        payload["saleStageId"] = _str_or_none(after.get("opportunity_stage"))

    if _str_or_none(before.get("forecast_type")) != _str_or_none(after.get("forecast_type")):
        payload["predictionType"] = _str_or_none(after.get("forecast_type"))

    nb = _normalize_expected_sign_month(before.get("expected_closing_date"))
    na = _normalize_expected_sign_month(after.get("expected_closing_date"))
    if nb != na:
        payload["expectedSignMonth"] = na

    if _money_compare_value(before.get("forecast_amount")) != _money_compare_value(after.get("forecast_amount")):
        payload["money"] = _coerce_money(after.get("forecast_amount"))

    # 丢单/取消补充字段：仅在前端随阶段变更一并提交时透传（前端保证此场景下 reason 必传）
    if "reason" in after:
        reason_code = _coerce_reason_code(after.get("reason"))
        if reason_code is not None:
            payload["reason"] = reason_code
        if "reasonDesc" in after or "reason_desc" in after:
            payload["reasonDesc"] = _str_or_none(after.get("reasonDesc", after.get("reason_desc")))
        competitor_id = _str_or_none(after.get("lostOrderCompetitors", after.get("competitor_id")))
        payload["lostOrderCompetitors"] = competitor_id if competitor_id is not None else "未知"

    return payload


def _crm_writeback_gateway_json_message(data: dict) -> str:
    """网关包体里的 ``message``（trim），无则空串。"""
    raw = data.get("message")
    if raw is None:
        return ""
    return str(raw).strip()


def _crm_writeback_gateway_envelope_ok(data: dict) -> bool:
    """网关统一 JSON：``success is False`` 或 ``code`` 非 200 视为业务失败；缺字段则兼容旧响应。"""
    if data.get("success") is False:
        return False
    code = data.get("code")
    if code is None:
        return True
    try:
        return int(code) == 200
    except (TypeError, ValueError):
        return str(code).strip() == "200"


def _crm_writeback_gateway_message_from_text(body: str) -> str:
    """从 HTTP 错误响应文本中尽量解析 ``message``。"""
    if not (body or "").strip():
        return ""
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(data, dict):
        return _crm_writeback_gateway_json_message(data)
    return ""


class CrmVisitWritebackClient:
    """拜访记录回写 HTTP 客户端（CBG / APAC / OLM / CHAITIN / WEBEYE / LIEPIN / WYWJ / ZHIPU 等）。"""
    
    def __init__(self, base_url: str = "http://salesforce:8080"):
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json"
        }
    
    
    def batch_task_create(self, task_batch_request: TaskBatchCreateRequest) -> Dict[str, Any]:
        """
        批量创建APAC任务
        
        Args:
            task_batch_request: 任务批量创建请求
        
        Returns:
            创建结果
        """
        url = f"{self.base_url}/tasks/batch"
        
        try:
            # 设置较长的超时时间来处理批量任务创建
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)  # 连接超时30秒，读取超时5分钟
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=self.headers, json=task_batch_request.model_dump())
                logger.info(f"调用批量创建任务，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"批量创建任务超时: {e}")
            return {"success": False, "message": f"批量创建任务超时: {e}"}
        except httpx.RequestError as e:
            logger.error(f"批量创建任务失败: {e}")
            return {"success": False, "message": f"批量创建任务失败: {e}"}
    
    def batch_cbg_visit_create(self, visit_requests: CbgVisitRecordBatchCreateRequest) -> Dict[str, Any]:
        """
        批量创建CBG日常对象
        
        Args:
            visit_requests: 日常对象创建请求列表
        
        Returns:
            创建结果
        """
        url = f"{self.base_url}/crm-custom/pingcap-cbg/sale-record/batch"
        
        try:
            # 设置较长的超时时间来处理批量日常对象创建
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                payload = visit_requests.model_dump(exclude_none=True)
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用CBG批量创建日常对象，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"CBG批量创建日常对象超时: {e}")
            return {"success": False, "message": f"CBG批量创建日常对象超时: {e}"}
        except httpx.RequestError as e:
            logger.error(f"CBG批量创建日常对象失败: {e}")
            return {"success": False, "message": f"CBG批量创建日常对象失败: {e}"}

    def batch_fenbeitong_visit_create(
        self, visit_requests: FenbeitongVisitRecordBatchCreateRequest
    ) -> Dict[str, Any]:
        """批量创建分贝通跟进记录。"""
        url = f"{self.base_url}/crm-custom/fenbeitong/sale-record/batch"

        try:
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                # exclude_none：未选外勤时不传 checkin_id
                payload = visit_requests.model_dump(exclude_none=True)
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用分贝通批量创建跟进记录，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"分贝通批量创建跟进记录超时: {e}")
            return {"success": False, "message": f"分贝通批量创建跟进记录超时: {e}"}
        except httpx.HTTPStatusError as e:
            logger.error(
                f"分贝通批量创建跟进记录 HTTP 错误: {e.response.status_code} {e.response.text}"
            )
            return {
                "success": False,
                "message": f"分贝通批量创建跟进记录 HTTP 错误: {e.response.status_code}",
                "data": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"分贝通批量创建跟进记录失败: {e}")
            return {"success": False, "message": f"分贝通批量创建跟进记录失败: {e}"}

    def fetch_fenbeitong_checkins(
        self, crmid: str, limit: int = 10
    ) -> Dict[str, Any]:
        """按 CRM 用户 ID 查询分贝通最近外勤打卡。"""
        url = f"{self.base_url}/crm-custom/fenbeitong/checkins"
        payload = {"crmid": crmid, "limit": limit}

        try:
            timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用分贝通外勤打卡查询，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"分贝通外勤打卡查询超时: {e}")
            return {"success": False, "message": f"分贝通外勤打卡查询超时: {e}"}
        except httpx.HTTPStatusError as e:
            logger.error(
                f"分贝通外勤打卡查询 HTTP 错误: {e.response.status_code} {e.response.text}"
            )
            return {
                "success": False,
                "message": f"分贝通外勤打卡查询 HTTP 错误: {e.response.status_code}",
                "data": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"分贝通外勤打卡查询失败: {e}")
            return {"success": False, "message": f"分贝通外勤打卡查询失败: {e}"}
    
    def batch_olm_visit_create(self, visit_requests: OlmVisitRecordBatchCreateRequest) -> Dict[str, Any]:
        """
        批量创建OLM拜访记录
        
        Args:
            visit_requests: 拜访记录创建请求列表
        
        Returns:
            创建结果
        """
        url = f"{self.base_url}/crm-xiaoshouyi/olm/batch"
        
        try:            
            # 设置较长的超时时间来处理批量拜访记录创建
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=self.headers, json=visit_requests.model_dump())
                logger.info(f"调用OLM批量创建拜访记录，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"OLM批量创建拜访记录超时: {e}")
            return {"success": False, "message": f"OLM批量创建拜访记录超时: {e}"}
        except httpx.RequestError as e:
            logger.error(f"OLM批量创建拜访记录失败: {e}")
            return {"success": False, "message": f"OLM批量创建拜访记录失败: {e}"}

    def batch_chaitin_visit_create(self, visit_requests: ChaitinVisitRecordBatchCreateRequest) -> Dict[str, Any]:
        """
        批量创建长亭拜访记录
        
        Args:
            visit_requests: 拜访记录创建请求列表
        
        Returns:
            创建结果
        """
        url = f"{self.base_url}/crm-custom/chaitin/batch"
        
        try:
            # 设置较长的超时时间来处理批量拜访记录创建
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=self.headers, json=visit_requests.model_dump())
                logger.info(f"调用长亭批量创建拜访记录，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"长亭批量创建拜访记录超时: {e}")
            return {"success": False, "message": f"长亭批量创建拜访记录超时: {e}"}
        except httpx.RequestError as e:
            logger.error(f"长亭批量创建拜访记录失败: {e}")
            return {"success": False, "message": f"长亭批量创建拜访记录失败: {e}"}

    def batch_webeye_visit_create(self, visit_requests: WebeyeVisitRecordBatchCreateRequest) -> Dict[str, Any]:
        """
        批量 upsert 网眼（简道云）拜访/跟进记录
        """
        url = f"{self.base_url}/crm-jiandaoyun/webeye/visit-record/batch"

        try:
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                # exclude_none：避免把空关联字段（lead_id/account_id）一并传给互斥场景
                payload = visit_requests.model_dump(exclude_none=True)
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用网眼批量拜访回写，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"网眼批量拜访回写超时: {e}")
            return {"success": False, "message": f"网眼批量拜访回写超时: {e}"}
        except httpx.HTTPStatusError as e:
            logger.error(f"网眼批量拜访回写 HTTP 错误: {e.response.status_code} {e.response.text}")
            return {
                "success": False,
                "message": f"网眼批量拜访回写 HTTP 错误: {e.response.status_code}",
                "response_text": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"网眼批量拜访回写失败: {e}")
            return {"success": False, "message": f"网眼批量拜访回写失败: {e}"}

    def batch_liepin_visit_create(
        self, visit_requests: LiepinVisitRecordBatchCreateRequest
    ) -> Dict[str, Any]:
        """批量回写猎聘拜访记录（``POST /crm-liepin/visit-record/batch``）。"""
        url = f"{self.base_url}/crm-liepin/visit-record/batch"

        try:
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                payload = visit_requests.model_dump(exclude_none=True)
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用猎聘批量拜访回写，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"猎聘批量拜访回写超时: {e}")
            return {"success": False, "message": f"猎聘批量拜访回写超时: {e}"}
        except httpx.HTTPStatusError as e:
            logger.error(
                f"猎聘批量拜访回写 HTTP 错误: {e.response.status_code} {e.response.text}"
            )
            return {
                "success": False,
                "message": f"猎聘批量拜访回写 HTTP 错误: {e.response.status_code}",
                "response_text": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"猎聘批量拜访回写失败: {e}")
            return {"success": False, "message": f"猎聘批量拜访回写失败: {e}"}

    def batch_wywj_visit_create(
        self, visit_requests: WywjVisitRecordBatchCreateRequest
    ) -> Dict[str, Any]:
        """批量回写网眼云捷拜访记录（``POST /crm-feishu/wywj/visit-record/batch``）。"""
        url = f"{self.base_url}/crm-feishu/wywj/visit-record/batch"

        try:
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                payload = visit_requests.model_dump(exclude_none=True)
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用网眼云捷批量拜访回写，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"网眼云捷批量拜访回写超时: {e}")
            return {"success": False, "message": f"网眼云捷批量拜访回写超时: {e}"}
        except httpx.HTTPStatusError as e:
            logger.error(
                f"网眼云捷批量拜访回写 HTTP 错误: {e.response.status_code} {e.response.text}"
            )
            return {
                "success": False,
                "message": f"网眼云捷批量拜访回写 HTTP 错误: {e.response.status_code}",
                "response_text": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"网眼云捷批量拜访回写失败: {e}")
            return {"success": False, "message": f"网眼云捷批量拜访回写失败: {e}"}

    def batch_zhipu_visit_create(
        self, visit_requests: ZhipuVisitRecordBatchCreateRequest
    ) -> Dict[str, Any]:
        """批量回写智谱跟进记录（``POST /crm-fxiaoke/zhipu/sale-record/batch``）。"""
        url = f"{self.base_url}/crm-fxiaoke/zhipu/sale-record/batch"

        try:
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                payload = visit_requests.model_dump(exclude_none=True)
                response = client.post(url, headers=self.headers, json=payload)
                logger.info(f"调用智谱批量拜访回写，返回: {response.text}")
                response.raise_for_status()
                return {"success": True, "data": response.json()}
        except httpx.TimeoutException as e:
            logger.error(f"智谱批量拜访回写超时: {e}")
            return {"success": False, "message": f"智谱批量拜访回写超时: {e}"}
        except httpx.HTTPStatusError as e:
            logger.error(
                f"智谱批量拜访回写 HTTP 错误: {e.response.status_code} {e.response.text}"
            )
            return {
                "success": False,
                "message": f"智谱批量拜访回写 HTTP 错误: {e.response.status_code}",
                "response_text": e.response.text,
            }
        except httpx.RequestError as e:
            logger.error(f"智谱批量拜访回写失败: {e}")
            return {"success": False, "message": f"智谱批量拜访回写失败: {e}"}


class CrmReviewWritebackClient:
    """CRM **review** 场景下的商机字段批量网关回写 HTTP 客户端。

    供成员 **保存草稿 / 提交** 等路径调用；``POST`` 至
    ``{CRM_WRITEBACK_API_URL}{CRM_WRITEBACK_REVIEW_PATH}``（与拜访回写路径分离，由网关路由具体 CRM）。
    与拜访回写 ``CrmVisitWritebackClient`` 隔离。
    """

    def __init__(self, base_url: str = "http://salesforce:8080"):
        self.base_url = base_url
        self.headers = {"Content-Type": "application/json"}

    def post_review_opportunity_updates(
        self, batch_request: ReviewOpportunityWritebackBatchRequest
    ) -> Dict[str, Any]:
        """对 ``batch_request.ops`` 逐条 POST；每条仅含 ``id`` 及相对 ``before_editable`` 有变化的可编辑字段。"""
        path = (getattr(settings, "CRM_WRITEBACK_REVIEW_PATH", None) or "").strip()
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.base_url}{path}"
        try:
            timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client:
                results: List[Any] = []
                posted = 0
                for op in batch_request.ops:
                    payload = review_op_to_gateway_update_json(op)
                    if not str(payload.get("id") or "").strip():
                        logger.error(
                            "CRM review writeback missing opportunity id: url=%s source=%s payload_keys=%s",
                            url,
                            batch_request.source,
                            list(payload.keys()),
                        )
                        return {
                            "success": False,
                            "message": "CRM review 回写缺少商机 id",
                            "response_text": None,
                        }
                    if set(payload.keys()) == {"id"}:
                        logger.info(
                            "CRM review writeback skip POST (no editable delta) id=%s source=%s",
                            payload.get("id"),
                            batch_request.source,
                        )
                        continue
                    response = client.post(
                        url,
                        headers=self.headers,
                        json=payload,
                    )
                    logger.info(
                        "CRM review writeback POST %s id=%s source=%s status=%s",
                        url,
                        payload.get("id"),
                        batch_request.source,
                        response.status_code,
                    )
                    response.raise_for_status()
                    try:
                        data = response.json()
                    except Exception:  # noqa: BLE001
                        results.append({"raw": response.text})
                        posted += 1
                        continue
                    if isinstance(data, dict) and not _crm_writeback_gateway_envelope_ok(data):
                        gw = _crm_writeback_gateway_json_message(data)
                        msg = gw or f"CRM 网关返回失败（code={data.get('code')!s}）"
                        logger.error(
                            "CRM review writeback gateway envelope rejected: url=%s id=%s "
                            "source=%s http_status=%s gateway_code=%s gateway_success=%s "
                            "message=%s gateway_response=%s",
                            url,
                            payload.get("id"),
                            batch_request.source,
                            response.status_code,
                            data.get("code"),
                            data.get("success"),
                            msg,
                            data,
                        )
                        return {
                            "success": False,
                            "message": msg,
                            "response_text": None,
                            "gateway_response": data,
                        }
                    results.append(data)
                    posted += 1
                return {
                    "success": True,
                    "data": {"results": results, "count": len(results), "posted_count": posted},
                    "posted_count": posted,
                }
        except httpx.TimeoutException as e:
            logger.error("CRM review writeback timeout: %s", e)
            return {"success": False, "message": f"CRM review 回写超时: {e}"}
        except httpx.HTTPStatusError as e:
            body = e.response.text[:4000] if e.response is not None else ""
            gw_msg = _crm_writeback_gateway_message_from_text(body)
            msg = gw_msg or f"CRM review 回写 HTTP 失败: {e}"
            logger.error(
                "CRM review writeback HTTP error: %s body=%s",
                e,
                body,
            )
            return {
                "success": False,
                "message": msg,
                "response_text": body or None,
            }
        except httpx.RequestError as e:
            logger.error("CRM review writeback request failed: %s", e)
            return {"success": False, "message": f"CRM review 回写请求失败: {e}"}


class CrmWritebackService:
    """CRM 回写服务：拜访与 review 两条链路分离，互不改对方配置与客户端。"""

    def __init__(self):
        base = settings.CRM_WRITEBACK_API_URL
        # 拜访记录回写（原逻辑）：仅使用 ``CrmVisitWritebackClient``；``self.client`` 仍指向该客户端以保持兼容
        self.client = CrmVisitWritebackClient(base)
        self._review_writeback_client = CrmReviewWritebackClient(base)
    
    def _parse_time_to_timestamp(self, date_value: datetime.date, time_str: str, record_id: int, time_label: str) -> Optional[int]:
        """
        解析时间字符串并与日期组合生成毫秒时间戳
        
        Args:
            date_value: 日期值
            time_str: 时间字符串，格式如 "HH:MM" 或 "HH:MM:SS"
            record_id: 记录ID，用于日志记录
            time_label: 时间标签（如"开始时间"、"结束时间"），用于日志记录
        
        Returns:
            毫秒时间戳，解析失败返回None
        """
        try:
            # 解析时间字符串，支持 "HH:MM" 或 "HH:MM:SS" 格式
            time_parts = time_str.strip().split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0
            second = int(time_parts[2]) if len(time_parts) > 2 else 0
            
            time_obj = time(hour, minute, second)
            datetime_obj = datetime.combine(date_value, time_obj)
            return int(datetime_obj.timestamp() * 1000)
        except (ValueError, AttributeError, IndexError) as e:
            logger.warning(f"记录 ID {record_id}：无法解析{time_label} {time_str}: {e}")
            return None
    
    def generate_visit_summary_content(self, record: CRMSalesVisitRecord) -> str:
        """
        根据拜访记录生成回写内容
        
        Args:
            record: 拜访记录
        
        Returns:
            格式化的回写内容
        """
        content_parts = []
        
        # 拜访基本信息
        content_parts.append(f"拜访及沟通日期: {record.visit_communication_date}")
        
        # if record.last_modified_time:
        #     formatted_time = convert_utc_to_local_timezone(record.last_modified_time)
        #     if formatted_time != "--":
        #         content_parts.append(f"创建时间: {formatted_time}")
        
        def _safe_str(v: Any) -> str:
            """将可能为 None/非字符串 的值安全转成字符串并 strip，避免出现 'None' 文本。"""
            if v is None:
                return ""
            if isinstance(v, str):
                return v.strip()
            return str(v).strip()

        followup_obj = resolve_followup_object_from_record(record)
        followup_object = (
            _safe_str(followup_obj.object_name if followup_obj else None)
            or _safe_str(record.account_name)
            or _safe_str(record.partner_name)
        )
        if followup_object:
            content_parts.append(f"跟进对象: {followup_object}")
        external_partner = _safe_str(getattr(record, "external_collaboration_partner_name", None))
        if external_partner:
            content_parts.append(f"外部协同伙伴: {external_partner}")
        
        # 处理联系人信息：优先使用contacts字段，否则使用旧字段
        if record.contacts and isinstance(record.contacts, list) and len(record.contacts) > 0:
            # 多个联系人
            for idx, contact in enumerate(record.contacts, 1):
                if isinstance(contact, dict):
                    contact_name = _safe_str(contact.get("name"))
                    contact_position = _safe_str(contact.get("position"))
                    if contact_name or contact_position:
                        if len(record.contacts) > 1:
                            # 多个联系人：将姓名和职位放在一起
                            if contact_position:
                                content_parts.append(f"联系人{idx}: {contact_name}（{contact_position}）")
                            else:
                                content_parts.append(f"联系人{idx}: {contact_name}")
                        else:
                            # 单个联系人：保持原格式，分开显示
                            content_parts.append(f"联系人职位: {contact_position}")
                            content_parts.append(f"联系人姓名: {contact_name}")
        else:
            # 兼容旧数据：使用单个联系人字段，保持原格式
            if record.contact_position or record.contact_name:
                content_parts.append(f"联系人职位: {_safe_str(record.contact_position)}")
                content_parts.append(f"联系人姓名: {_safe_str(record.contact_name)}")
        
        if record.collaborative_participants:
            # 处理协同参与人数据，支持多种格式
            from app.utils.participants_utils import format_collaborative_participants_names
            participant_names_str = format_collaborative_participants_names(record.collaborative_participants)
            if participant_names_str:
                content_parts.append(f"协同参与人（内部人员）: {participant_names_str}")
        
        # 跟进记录
        if record.followup_record:
            content_parts.append("跟进记录:")
            content_parts.append(record.followup_record)
        
        # 下一步计划
        if record.next_steps:
            content_parts.append("下一步计划:")
            content_parts.append(record.next_steps)
        
        # 风险/备注
        if record.remarks:
            content_parts.append("风险/备注:")
            content_parts.append(record.remarks)
        
        return "\n".join(content_parts)
    
    def generate_task_requests(self, visit_records: List[CRMSalesVisitRecord]) -> List[TaskCreateRequest]:
        """
        根据拜访记录生成APAC任务创建请求列表
        
        Args:
            visit_records: 拜访记录列表
        
        Returns:
            任务创建请求列表
        """
        task_requests = []
        
        def _norm_id(v: Optional[str]) -> Optional[str]:
            if v is None:
                return None
            s = str(v).strip()
            return s if s else None

        def _legacy_partner_id(record: CRMSalesVisitRecord) -> Optional[str]:
            """
            兼容外部协同口径：
            - 历史：partner_id 表示合作伙伴
            - 现在：客户跟进时 partner_id 可能为空，外部协同方写在 external_collaboration_partner_id
            为保持旧 writeback 语义：当 partner_id 为空时，用 external_collaboration_partner_id 作为等价 partner。
            """
            return _norm_id(record.partner_id) or _norm_id(
                getattr(record, "external_collaboration_partner_id", None)
            )

        for record in visit_records:
            # 确定what_id（优先级：商机 > 客户 > 合作伙伴）
            what_id = None
            if record.opportunity_id:
                what_id = record.opportunity_id
            elif record.account_id:
                what_id = record.account_id
            else:
                what_id = _legacy_partner_id(record)
            
            if not what_id:
                continue
            
            # 检查必填的拜访主题
            if not record.subject:
                logger.warning(f"跳过记录 ID {record.id}：缺少必填的拜访主题")
                continue
            
            # 生成任务主题
            subject = record.subject
            
            # 生成进度记录（跟进记录）
            progress = record.followup_record_en or record.followup_record or ""
            if len(progress) > 255:
                progress = progress[:252] + "..."

            # 生成风险与下一步
            risk_and_next_step = record.next_steps_en or record.next_steps or ""
            if len(risk_and_next_step) > 255:
                risk_and_next_step = risk_and_next_step[:252] + "..."
            
            # 设置活动日期为拜访日期
            activity_date = record.visit_communication_date.strftime("%Y-%m-%d")
            
            task_request = TaskCreateRequest(
                subject=subject,
                status="Open",
                what_id=what_id,
                progress=progress,
                risk_and_next_step=risk_and_next_step,
                priority="Normal",
                activity_date=activity_date
            )
            
            task_requests.append(task_request)
        
        return task_requests

    def _map_visit_method_to_cbg_record_type(self, visit_communication_method: Optional[str]) -> str:
        """
        将历史/现有「拜访方式」映射为 CBG「跟进类型」。

        兼容口径：
        - 历史数据：线下会议、线上会议、来我司拜访、饭局聚会 -> 常规拜访
        - 历史数据：电话/录音、其他 -> 电话/微信跟进
        """
        method = (visit_communication_method or "").strip()

        if method in {
            # 历史数据
            "电话/录音",
            "其他",
            # 新口径
            CbgVisitRecordType.CUSTOMER_PHONE.value,
        }:
            return CbgVisitRecordType.CUSTOMER_PHONE.value

        if method in {
            # 历史数据
            "线下会议",
            "线上会议",
            "来我司拜访",
            "饭局聚会",
            # 新口径
            CbgVisitRecordType.CUSTOMER_VISIT.value,
        }:
            return CbgVisitRecordType.CUSTOMER_VISIT.value

        # 默认兜底：常规拜访
        return CbgVisitRecordType.CUSTOMER_VISIT.value
    
    def generate_cbg_visit_requests(self, session: Session, visit_records: List[CRMSalesVisitRecord]) -> CbgVisitRecordBatchCreateRequest:
        """
        根据拜访记录生成CBG日常对象创建请求列表。

        线索跟进（followup_object_type=lead）写入 ``lead_ids``，不传 ``account_ids``。
        
        Args:
            session: 数据库会话
            visit_records: 拜访记录列表
        
        Returns:
            CBG日常对象创建请求列表
        """
        visit_requests = CbgVisitRecordBatchCreateRequest(records=[])
        
        def _norm_id(v: Optional[str]) -> Optional[str]:
            if v is None:
                return None
            s = str(v).strip()
            return s if s else None

        def _legacy_partner_id(record: CRMSalesVisitRecord) -> Optional[str]:
            return _norm_id(record.partner_id) or _norm_id(
                getattr(record, "external_collaboration_partner_id", None)
            )

        for record in visit_records:
            crm_user_id = None
            ask_id_str = None
            if record.recorder_id:
                try:
                    # 将32位UUID转换为36位字符串（带连字符）
                    # recorder_id是UUID对象，转成字符串会自动变成36位格式
                    ask_id_str = str(record.recorder_id)
                    
                    # 从user_profiles表查询crm_user_id作为CRM用户ID
                    sql_query = text("""
                        SELECT crm_user_id FROM user_profiles WHERE oauth_user_id = :ask_id
                    """)
                    
                    result = session.exec(sql_query, params={"ask_id": ask_id_str}).first()
                        
                    if result:
                        # 单列查询，first() 返回 Row/tuple，直接取第 0 列即可
                        crm_user_id = str(result[0]) if result[0] is not None else None
                    else:
                        logger.warning(f"记录 ID {record.id}：未找到recorder_id {ask_id_str} 对应的CRM用户ID")
                except Exception as e:
                    logger.warning(f"记录 ID {record.id}：查询CRM用户ID失败: {e}")
            
            if not crm_user_id:
                logger.warning(
                    f"记录 ID {record.id}：未找到recorder_id {ask_id_str} 对应的CRM用户ID，将使用系统管理员ID"
                )
            
            record_type = self._map_visit_method_to_cbg_record_type(record.visit_communication_method)
            
            legacy_partner_id = _legacy_partner_id(record)
            account_ids = [
                x
                for x in [_norm_id(record.account_id), legacy_partner_id]
                if x is not None
            ]
            opportunity_ids = [x for x in [_norm_id(record.opportunity_id)] if x is not None]

            lead_ids: list[str] = []
            followup_obj = resolve_followup_object_from_record(record)
            if followup_obj and followup_obj.object_type == FOLLOWUP_OBJECT_TYPE_LEAD:
                lead_id = _norm_id(followup_obj.object_id)
                if lead_id:
                    lead_ids.append(lead_id)
                # 线索与客户互斥：线索跟进不传 account_ids
                account_ids = []

            # 客户/伙伴、商机、线索至少一类有值；线索跟进走 lead_ids
            if not account_ids and not opportunity_ids and not lead_ids:
                logger.warning(
                    f"记录 ID {record.id}：account_id/partner_id、opportunity_id 和 lead_id 均为空，跳过创建CBG日常对象"
                )
                continue
            visit_request = CbgVisitRecordCreateRequest(
                    record_type=record_type,
                    content=self.generate_visit_summary_content(record),
                    account_ids=account_ids or None,
                    opportunity_ids=opportunity_ids or None,
                    lead_ids=lead_ids or None,
                    owner_user_id=crm_user_id,
                    source_record_id=str(record.record_id or record.id),
            )
            
            visit_requests.records.append(visit_request)
            
        return visit_requests

    def generate_fenbeitong_visit_requests(
        self, session: Session, visit_records: List[CRMSalesVisitRecord]
    ) -> FenbeitongVisitRecordBatchCreateRequest:
        """根据拜访记录生成分贝通跟进记录创建请求列表。

        ``record_type`` 直接使用前端已对齐的 ``visit_communication_method``，不做额外映射。
        """
        visit_requests = FenbeitongVisitRecordBatchCreateRequest(records=[])

        def _norm_id(v: Optional[str]) -> Optional[str]:
            if v is None:
                return None
            s = str(v).strip()
            return s if s else None

        def _legacy_partner_id(record: CRMSalesVisitRecord) -> Optional[str]:
            return _norm_id(record.partner_id) or _norm_id(
                getattr(record, "external_collaboration_partner_id", None)
            )

        for record in visit_records:
            ask_id_str = None
            crm_user_id = None
            if record.recorder_id:
                try:
                    ask_id_str = str(record.recorder_id)
                    sql_query = text(
                        """
                        SELECT crm_user_id FROM user_profiles WHERE oauth_user_id = :ask_id
                        """
                    )
                    result = session.exec(sql_query, params={"ask_id": ask_id_str}).first()
                    if result:
                        crm_user_id = str(result[0]) if result[0] is not None else None
                    else:
                        logger.warning(
                            f"记录 ID {record.id}：未找到recorder_id {ask_id_str} 对应的CRM用户ID"
                        )
                except Exception as e:
                    logger.warning(f"记录 ID {record.id}：查询CRM用户ID失败: {e}")

            if not crm_user_id:
                logger.warning(
                    f"记录 ID {record.id}：未找到recorder_id {ask_id_str} 对应的CRM用户ID，"
                    "owner_user_id 将为空"
                )

            record_type = _norm_id(record.visit_communication_method)
            if not record_type:
                logger.warning(
                    f"记录 ID {record.id}：visit_communication_method 为空，跳过创建分贝通跟进记录"
                )
                continue

            legacy_partner_id = _legacy_partner_id(record)
            account_ids = [
                x
                for x in [_norm_id(record.account_id), legacy_partner_id]
                if x is not None
            ]
            opportunity_ids = [
                x for x in [_norm_id(record.opportunity_id)] if x is not None
            ]

            if not account_ids and not opportunity_ids:
                logger.warning(
                    f"记录 ID {record.id}：account_id/partner_id 和 opportunity_id 均为空，"
                    "跳过创建分贝通跟进记录"
                )
                continue

            visit_request = FenbeitongVisitRecordCreateRequest(
                record_type=record_type,
                content=self.generate_visit_summary_content(record),
                account_ids=account_ids or None,
                opportunity_ids=opportunity_ids or None,
                owner_user_id=crm_user_id,
                source_record_id=str(record.record_id or record.id),
                checkin_id=_norm_id(getattr(record, "field_check_in_id", None)),
            )
            visit_requests.records.append(visit_request)

        return visit_requests
    
    def generate_olm_visit_requests(self, session: Session, visit_records: List[CRMSalesVisitRecord]) -> OlmVisitRecordBatchCreateRequest:
        """
        根据拜访记录生成OLM拜访记录创建请求列表
        
        Args:
            session: 数据库会话
            visit_records: 拜访记录列表
        
        Returns:
            OLM拜访记录创建请求列表
        """
        visit_requests = OlmVisitRecordBatchCreateRequest(visit_records=[])
        
        for record in visit_records:            
            # 签到时间：拜访日期 + 开始时间
            sign_in_date = self._parse_time_to_timestamp(
                record.visit_communication_date, 
                record.visit_start_time, 
                record.id, 
                "开始时间"
            )
            
            # 签退时间：拜访日期 + 结束时间
            sign_out_date = self._parse_time_to_timestamp(
                record.visit_communication_date, 
                record.visit_end_time, 
                record.id, 
                "结束时间"
            )
            
            # 本次拜访目的（最多300个字符）：映射到跟进记录
            followup = record.followup_record or record.followup_content
            custom_item5 = followup[:300] if followup else None
            
            # 拜访事项及结果记录（最多300个字符）：映射到下一步计划
            custom_item2 = record.next_steps[:300] if record.next_steps else None
            
            # 是否新客户：1=是, 2=否
            custom_item6 = 1 if record.is_first_visit else 2
            
            # 所有人ID和部门ID（从user_profiles表查询crm_user_id，再从crm_user表查询department_id）
            owner_id = None
            dim_depart = None
            if record.recorder_id:
                try:
                    # 将32位UUID转换为36位字符串（带连字符）
                    # recorder_id是UUID对象，转成字符串会自动变成36位格式
                    ask_id_str = str(record.recorder_id)
                    
                    # 使用原生SQL查询，一次性获取crm_user_id和department_id
                    sql_query = text("""
                        SELECT u.crm_user_id, cu.department_id
                        FROM user_profiles u
                        LEFT JOIN crm_user cu ON u.crm_user_id = cu.unique_id
                        WHERE u.oauth_user_id = :ask_id
                    """)
                    
                    result = session.exec(sql_query, params={"ask_id": ask_id_str}).first()
                    
                    if result:
                        crm_user_id, depart_id = result
                        if crm_user_id:
                            try:
                                owner_id = int(crm_user_id)
                                if depart_id:
                                    dim_depart = int(depart_id)
                                else:
                                    logger.warning(f"记录 ID {record.id}：未找到crm_user_id {crm_user_id} 对应的部门信息")
                            except (ValueError, TypeError) as e:
                                logger.warning(f"记录 ID {record.id}：数据格式错误: crm_user_id={crm_user_id}, depart_id={depart_id}, 错误: {e}")
                        else:
                            logger.warning(f"记录 ID {record.id}：crm_user_id为空")
                    else:
                        logger.warning(f"记录 ID {record.id}：未找到recorder_id {ask_id_str} 对应的用户")
                except Exception as e:
                    logger.warning(f"记录 ID {record.id}：查询用户信息失败: {e}")
            
            # 创建时间：转换为北京时间（东八区）
            created_at = None
            if record.last_modified_time:
                # 确保时间是UTC时间，然后转换为北京时间
                if record.last_modified_time.tzinfo is None:
                    # 如果没有时区信息，假设是UTC时间
                    utc_time = record.last_modified_time.replace(tzinfo=timezone.utc)
                else:
                    utc_time = record.last_modified_time.astimezone(timezone.utc)
                
                # 转换为北京时间（UTC+8）
                beijing_tz = timezone(timedelta(hours=8))
                beijing_time = utc_time.astimezone(beijing_tz)
                created_at = int(beijing_time.timestamp() * 1000)
            
            # 从attachment中解析签到地址和拜访拍照时间
            sign_in_address = None
            visit_photo_time = None
            if record.attachment:
                attachment = VisitAttachment.from_legacy_value(record.attachment)
                sign_in_address = attachment.location or '暂无'
                # taken_at字段是str类型的时间（大部分情况下是北京时间），需要转为毫秒时间戳
                visit_photo_time = None
                if attachment.taken_at:
                    try:
                        # 优先尝试完整格式(含时区)
                        from dateutil import parser
                        dt = parser.isoparse(attachment.taken_at)
                        # 如果dt没有tzinfo，则假定为北京时间（东八区）
                        if dt.tzinfo is None:
                            beijing_tz = timezone(timedelta(hours=8))
                            dt = dt.replace(tzinfo=beijing_tz)
                        # 转为unix毫秒时间戳
                        visit_photo_time = int(dt.timestamp() * 1000)
                    except Exception:
                        # 解析失败则为None
                        visit_photo_time = None
            
            # 构建请求（account 优先客户 ID，否则伙伴/外部协同 ID；须为可解析的整数）
            legacy_partner_id = _str_or_none(record.partner_id) or _str_or_none(
                getattr(record, "external_collaboration_partner_id", None)
            )
            account = _safe_parse_int_id(record.account_id) or _safe_parse_int_id(
                legacy_partner_id
            )
            if account is None and (record.account_id or legacy_partner_id):
                logger.warning(
                    f"记录 ID {record.id}：account/partner ID 无法解析为整数: "
                    f"account_id={record.account_id!r}, legacy_partner_id={legacy_partner_id!r}"
                )
            visit_request = OlmVisitRecordCreateRequest(
                account=account,
                dim_depart=dim_depart,
                custom_item3=record.visit_communication_method,
                sign_in_date=sign_in_date,
                sign_out_date=sign_out_date,
                custom_item5=custom_item5,
                custom_item2=custom_item2,
                custom_item6=custom_item6,
                owner_id=owner_id,
                source_record_id=str(record.record_id or record.id),
                created_by=owner_id,
                created_at=created_at,
                sign_in_address=sign_in_address,
                custom_item7=visit_photo_time
            )
            
            visit_requests.visit_records.append(visit_request)

        return visit_requests
    
    
    def generate_chaitin_visit_requests(self, session: Session, visit_records: List[CRMSalesVisitRecord]) -> ChaitinVisitRecordBatchCreateRequest:
        """
        根据拜访记录生成长亭拜访记录创建请求列表
        
        Args:
            session: 数据库会话
            visit_records: 拜访记录列表
        
        Returns:
            长亭拜访记录创建请求列表
        """
        visit_requests = ChaitinVisitRecordBatchCreateRequest(followup_records=[])
        
        for record in visit_records:
            user_name = None
            # 长亭CRM用户名（从user_profiles表查询crm_user_id，再从crm_user表查询长亭CRM用户名）
            if record.recorder_id:
                try:
                    # 将32位UUID转换为36位字符串（带连字符）
                    # recorder_id是UUID对象，转成字符串会自动变成36位格式
                    ask_id_str = str(record.recorder_id)
                    
                    # 使用原生SQL查询，一次性获取crm_user_id和长亭CRM用户名
                    sql_query = text("""
                        SELECT up.crm_user_id, cu.unique_id as user_name
                        FROM user_profiles up
                        LEFT JOIN crm_user cu ON up.crm_user_id = cu.unique_id
                        WHERE up.oauth_user_id = :ask_id
                    """)
                    
                    result = session.exec(sql_query, params={"ask_id": ask_id_str}).first()
                    if result:
                        crm_user_id, user_name = result
                        if user_name:
                            user_name = str(user_name)
                        else:
                            logger.warning(f"记录 ID {record.id}：未找到crm_user_id {crm_user_id} 对应的长亭CRM用户名")
                    else:
                        logger.warning(f"记录 ID {record.id}：未找到recorder_id {ask_id_str} 对应的用户")
                except Exception as e:
                    logger.warning(f"记录 ID {record.id}：查询长亭CRM用户名失败: {e}")

            if not user_name:
                logger.warning(f"记录 ID {record.id} 没有有效的长亭CRM用户名，跳过回写")
                continue
            
            # 构建请求
            legacy_partner_id = record.partner_id or getattr(record, "external_collaboration_partner_id", None)
            visit_request = ChaitinVisitRecordCreateRequest(
                company_id=record.account_id if record.account_id else legacy_partner_id,
                content=f"跟进记录：{record.followup_record_zh or record.followup_record}\n下一步计划：{record.next_steps_zh or record.next_steps}",
                username=user_name,
                project_id=record.opportunity_id,
                source_record_id=str(record.record_id or record.id)
            )
            
            visit_requests.followup_records.append(visit_request)
        return visit_requests

    def _resolve_webeye_recorder_username(
        self, session: Session, record: CRMSalesVisitRecord
    ) -> Optional[str]:
        """将 ASK recorder_id 解析为简道云人员 username（crm_user.unique_id）。"""
        if not record.recorder_id:
            return None
        try:
            ask_id_str = str(record.recorder_id)
            sql_query = text("""
                SELECT cu.unique_id AS user_name
                FROM user_profiles up
                LEFT JOIN crm_user cu ON up.crm_user_id = cu.unique_id
                WHERE up.oauth_user_id = :ask_id
            """)
            result = session.exec(sql_query, params={"ask_id": ask_id_str}).first()
            if result:
                user_name = result[0] if not isinstance(result, str) else result
                if user_name:
                    return str(user_name)
                logger.warning(f"记录 ID {record.id}：未找到对应的网眼 CRM 用户名")
            else:
                logger.warning(f"记录 ID {record.id}：未找到 recorder_id {ask_id_str} 对应的用户")
        except Exception as e:
            logger.warning(f"记录 ID {record.id}：查询网眼 CRM 用户名失败: {e}")
        return None

    def _lookup_webeye_account(
        self, session: Session, entity_id: str
    ) -> Optional[CRMAccount]:
        eid = _str_or_none(entity_id)
        if not eid:
            return None
        return session.exec(
            select(CRMAccount).where(
                CRMAccount.unique_id == eid,
                or_(CRMAccount.delete_flag == 0, CRMAccount.delete_flag.is_(None)),
            )
        ).first()

    def _lookup_webeye_lead(self, session: Session, entity_id: str) -> Optional[CRMLead]:
        eid = _str_or_none(entity_id)
        if not eid:
            return None
        return session.exec(
            select(CRMLead).where(
                CRMLead.unique_id == eid,
                CRMLead.is_deleted == 0,
            )
        ).first()

    def generate_webeye_visit_requests(
        self, session: Session, visit_records: List[CRMSalesVisitRecord]
    ) -> WebeyeVisitRecordBatchCreateRequest:
        """
        根据拜访记录生成网眼（简道云）拜访回写请求。

        以 ``followup_object_*``（含历史 account/partner 推断）决定场景：
        - lead → 查 ``crm_leads``，只传 ``lead_id``；受 ``CRM_WEBEYE_WRITEBACK_LEAD_ENABLED``
          控制，默认关闭（不回写线索跟进）
        - end_customer / partner → 查 ``crm_accounts``，只传 ``account_id``
        """
        visit_requests = WebeyeVisitRecordBatchCreateRequest(visit_records=[])

        for record in visit_records:
            followup_obj = resolve_followup_object_from_record(record)
            if not followup_obj:
                logger.warning(
                    f"记录 ID {record.id}：无法解析跟进对象（followup_object/account/partner 均为空），跳过网眼回写"
                )
                continue

            object_type = followup_obj.object_type
            entity_id = followup_obj.object_id
            local_name = followup_obj.object_name

            recorder_username = self._resolve_webeye_recorder_username(session, record)
            if not recorder_username:
                logger.warning(
                    f"记录 ID {record.id}：未解析到简道云跟进人 username，跳过网眼回写"
                )
                continue

            followup = (
                record.followup_record_zh
                or record.followup_record
                or record.followup_content
            )
            next_steps = record.next_steps_zh or record.next_steps
            visit_date = (
                record.visit_communication_date.isoformat()
                if record.visit_communication_date
                else None
            )
            source_record_id = str(record.record_id or record.id)

            if object_type == FOLLOWUP_OBJECT_TYPE_LEAD:
                if not settings.CRM_WEBEYE_WRITEBACK_LEAD_ENABLED:
                    logger.info(
                        f"记录 ID {record.id}：跟进对象为 lead（线索），"
                        f"CRM_WEBEYE_WRITEBACK_LEAD_ENABLED 为关闭，跳过网眼回写"
                    )
                    continue
                lead = self._lookup_webeye_lead(session, entity_id)
                if lead is None:
                    logger.warning(
                        f"记录 ID {record.id}：在 crm_leads 中未找到 "
                        f"followup_object_id={entity_id}，跳过网眼回写"
                    )
                    continue
                lead_serial = None
                if isinstance(lead.extra, dict):
                    lead_serial = _str_or_none(lead.extra.get("lead_serial_number"))
                display_name = (
                    local_name
                    or _str_or_none(lead.company_name)
                    or _str_or_none(lead.lead_name)
                )
                visit_request = WebeyeVisitRecordCreateRequest(
                    source_record_id=source_record_id,
                    lead_id=lead.unique_id,
                    lead_serial_number=lead_serial,
                    account_name=display_name,
                    recorder_id=recorder_username,
                    recorder=record.recorder,
                    visit_communication_date=visit_date,
                    visit_communication_method=record.visit_communication_method,
                    followup_record=followup,
                    next_steps=next_steps,
                    remarks=record.remarks,
                )
            elif object_type in (
                FOLLOWUP_OBJECT_TYPE_END_CUSTOMER,
                FOLLOWUP_OBJECT_TYPE_PARTNER,
            ):
                account = self._lookup_webeye_account(session, entity_id)
                if account is None:
                    logger.warning(
                        f"记录 ID {record.id}：在 crm_accounts 中未找到 "
                        f"followup_object_id={entity_id} (type={object_type})，跳过网眼回写"
                    )
                    continue
                visit_request = WebeyeVisitRecordCreateRequest(
                    source_record_id=source_record_id,
                    account_id=account.unique_id,
                    customer_code=account.customer_code,
                    account_short_name=account.customer_abbreviation,
                    account_name=local_name or account.customer_name,
                    opportunity_id=_str_or_none(record.opportunity_id),
                    opportunity_name=record.opportunity_name,
                    recorder_id=recorder_username,
                    recorder=record.recorder,
                    visit_communication_date=visit_date,
                    visit_communication_method=record.visit_communication_method,
                    followup_record=followup,
                    next_steps=next_steps,
                    remarks=record.remarks,
                )
            else:
                logger.warning(
                    f"记录 ID {record.id}：不支持的跟进对象类型 {object_type!r}，跳过网眼回写"
                )
                continue

            visit_requests.visit_records.append(visit_request)

        return visit_requests

    def _resolve_recorder_crm_user_id(
        self, session: Session, record: CRMSalesVisitRecord
    ) -> Optional[str]:
        """将 recorder_id 解析为 CRM 用户 ID（user_profiles.crm_user_id）。

        与 CBG / 分贝通 / 猎聘 / 智谱等拜访回写一致：``str(recorder_id)``（UUID 对象会变成 36 位带连字符）
        匹配 ``user_profiles.oauth_user_id``。
        """
        if not record.recorder_id:
            return None
        try:
            ask_id_str = str(record.recorder_id)
            sql_query = text(
                """
                SELECT crm_user_id FROM user_profiles WHERE oauth_user_id = :ask_id
                """
            )
            result = session.exec(sql_query, params={"ask_id": ask_id_str}).first()
            if result and result[0] is not None:
                return str(result[0])
            logger.warning(
                f"记录 ID {record.id}：未找到 recorder_id {ask_id_str} 对应的 CRM 用户 ID"
            )
        except Exception as e:
            logger.warning(f"记录 ID {record.id}：查询 CRM 用户 ID 失败: {e}")
        return None

    @staticmethod
    def _format_liepin_last_modified_time(dt: Optional[datetime]) -> Optional[str]:
        """格式化为猎聘网关约定的 UTC 时间字符串 ``YYYY-MM-DD HH:MM:SS``。"""
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_liepin_contacts(
        record: CRMSalesVisitRecord,
    ) -> Tuple[Optional[List[LiepinVisitContact]], Optional[List[str]]]:
        """规范化猎聘回写 contacts，并提取 contact_id 列表。

        优先 ``record.contacts``；否则回退旧字段 contact_id/name/position。
        转发明文约定：保留规范化 contacts，并附带 ``contact_id: ["id1", ...]``。
        """
        contacts: List[LiepinVisitContact] = []

        raw_contacts = record.contacts
        if isinstance(raw_contacts, list) and len(raw_contacts) > 0:
            for item in raw_contacts:
                if not isinstance(item, dict):
                    continue
                contact = LiepinVisitContact(
                    contact_id=_str_or_none(item.get("contact_id")),
                    name=_str_or_none(item.get("name")),
                    position=_str_or_none(item.get("position")),
                )
                if contact.contact_id or contact.name or contact.position:
                    contacts.append(contact)
        else:
            legacy = LiepinVisitContact(
                contact_id=_str_or_none(record.contact_id),
                name=_str_or_none(record.contact_name),
                position=_str_or_none(record.contact_position),
            )
            if legacy.contact_id or legacy.name or legacy.position:
                contacts.append(legacy)

        if not contacts:
            return None, None

        contact_ids = [
            c.contact_id for c in contacts if c.contact_id
        ]
        return contacts, contact_ids or None

    def generate_liepin_visit_requests(
        self, session: Session, visit_records: List[CRMSalesVisitRecord]
    ) -> LiepinVisitRecordBatchCreateRequest:
        """
        根据拜访记录生成猎聘拜访回写请求。

        字段对齐 ``POST /crm-liepin/visit-record``；跟进对象优先 ``followup_object_*``，
        否则回退 account/partner。缺必填字段的记录跳过。
        """
        visit_requests = LiepinVisitRecordBatchCreateRequest(visits=[])

        for record in visit_records:
            followup_obj = resolve_followup_object_from_record(record)
            if not followup_obj:
                logger.warning(
                    f"记录 ID {record.id}：无法解析跟进对象（followup_object/account/partner 均为空），"
                    "跳过猎聘回写"
                )
                continue

            object_name = _str_or_none(followup_obj.object_name)
            if not object_name:
                logger.warning(
                    f"记录 ID {record.id}：followup_object_name 为空，跳过猎聘回写"
                )
                continue

            if not record.visit_communication_date:
                logger.warning(
                    f"记录 ID {record.id}：visit_communication_date 为空，跳过猎聘回写"
                )
                continue

            visit_method = _str_or_none(record.visit_communication_method)
            if not visit_method:
                logger.warning(
                    f"记录 ID {record.id}：visit_communication_method 为空，跳过猎聘回写"
                )
                continue

            # 简易版仅有 followup_content 时回退到 followup_record。
            followup_record = _str_or_none(
                record.followup_record_zh
                or record.followup_record
                or record.followup_content
            )
            if not followup_record:
                logger.warning(
                    f"记录 ID {record.id}：followup_record 为空，跳过猎聘回写"
                )
                continue

            next_steps = _str_or_none(record.next_steps_zh or record.next_steps)
            if not next_steps:
                logger.warning(
                    f"记录 ID {record.id}：next_steps 为空，跳过猎聘回写"
                )
                continue

            last_modified = self._format_liepin_last_modified_time(
                record.last_modified_time
            )
            if not last_modified:
                logger.warning(
                    f"记录 ID {record.id}：last_modified_time 为空，跳过猎聘回写"
                )
                continue

            contacts, contact_ids = self._normalize_liepin_contacts(record)

            visit_requests.visits.append(
                LiepinVisitRecordCreateRequest(
                    record_id=str(record.record_id or record.id),
                    followup_object_type=followup_obj.object_type,
                    followup_object_id=followup_obj.object_id,
                    followup_object_name=object_name,
                    opportunity_id=_str_or_none(record.opportunity_id),
                    recorder=_str_or_none(record.recorder),
                    recorder_id=self._resolve_recorder_crm_user_id(session, record),
                    visit_communication_date=record.visit_communication_date.isoformat(),
                    visit_communication_method=visit_method,
                    followup_record=followup_record,
                    next_steps=next_steps,
                    last_modified_time=last_modified,
                    contacts=contacts,
                    contact_id=contact_ids,
                    visit_url=_str_or_none(record.visit_url),
                )
            )

        return visit_requests

    def _resolve_feishu_open_ids(
        self, session: Session, user_ids: List[str]
    ) -> Dict[str, str]:
        """批量将用户标识解析为飞书/Lark open_id（ou_...）。

        入参兼容两种常见键：
        - ``users.id`` / ``oauth_accounts.user_id``（拜访 ``recorder_id``）
        - ``user_profiles.oauth_user_id``（协同参与人 ``ask_id``）
        """
        from sqlalchemy import bindparam

        unique_ids = list(dict.fromkeys(uid for uid in user_ids if uid))
        if not unique_ids:
            return {}
        # oauth_accounts.user_id 实际以无连字符 CHAR(32) 存储
        query_user_ids = list(
            dict.fromkeys(uid.replace("-", "") for uid in unique_ids if uid)
        )
        result: Dict[str, str] = {}
        by_compact: Dict[str, str] = {}
        try:
            sql_by_user = text(
                """
                SELECT user_id, open_id
                FROM oauth_accounts
                WHERE user_id IN :user_ids
                  AND open_id IS NOT NULL
                  AND provider IN ('feishu', 'lark')
                """
            ).bindparams(bindparam("user_ids", expanding=True))
            rows = session.exec(
                sql_by_user, params={"user_ids": query_user_ids}
            ).all()
            for row in rows:
                if not row:
                    continue
                uid, open_id = row[0], row[1]
                if uid is None or open_id is None:
                    continue
                compact = str(uid).replace("-", "")
                if compact not in by_compact:
                    by_compact[compact] = str(open_id)
        except Exception as e:
            logger.warning(f"按 user_id 解析飞书 open_id 失败: {e}")

        unresolved = [
            uid for uid in unique_ids if uid.replace("-", "") not in by_compact
        ]
        if unresolved:
            try:
                sql_by_oauth = text(
                    """
                    SELECT up.oauth_user_id, oa.open_id
                    FROM user_profiles up
                    INNER JOIN oauth_accounts oa ON oa.user_id = up.user_id
                    WHERE up.oauth_user_id IN :oauth_user_ids
                      AND oa.open_id IS NOT NULL
                      AND oa.provider IN ('feishu', 'lark')
                    """
                ).bindparams(bindparam("oauth_user_ids", expanding=True))
                rows = session.exec(
                    sql_by_oauth, params={"oauth_user_ids": unresolved}
                ).all()
                for row in rows:
                    if not row:
                        continue
                    oauth_uid, open_id = row[0], row[1]
                    if not oauth_uid or not open_id:
                        continue
                    # 直接按入参 oauth_user_id 回填
                    key = str(oauth_uid)
                    compact = key.replace("-", "")
                    if compact not in by_compact:
                        by_compact[compact] = str(open_id)
                    if key not in result:
                        result[key] = str(open_id)
            except Exception as e:
                logger.warning(f"按 oauth_user_id 解析飞书 open_id 失败: {e}")

        for uid in unique_ids:
            if uid in result:
                continue
            open_id = by_compact.get(uid.replace("-", ""))
            if open_id:
                result[uid] = open_id
        return result

    def _resolve_wywj_recorder_open_id(
        self,
        session: Session,
        record: CRMSalesVisitRecord,
        open_id_by_user_id: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """将 recorder_id 解析为飞书 open_id。"""
        if not record.recorder_id:
            return None
        ask_id = str(record.recorder_id)
        mapping = open_id_by_user_id
        if mapping is None:
            mapping = self._resolve_feishu_open_ids(session, [ask_id])
        open_id = mapping.get(ask_id)
        if not open_id:
            logger.warning(
                f"记录 ID {record.id}：未找到 recorder_id {ask_id} 对应的飞书 open_id"
            )
        return open_id

    def _resolve_wywj_collaborative_open_ids(
        self,
        session: Session,
        record: CRMSalesVisitRecord,
        open_id_by_user_id: Optional[Dict[str, str]] = None,
    ) -> Optional[List[str]]:
        """将协同参与人 ask_id 解析为飞书 open_id 列表。"""
        from app.utils.participants_utils import parse_collaborative_participants_list

        participants = parse_collaborative_participants_list(
            record.collaborative_participants
        )
        ask_ids: List[str] = []
        for item in participants:
            if not isinstance(item, dict):
                continue
            ask_id = _str_or_none(item.get("ask_id"))
            if ask_id:
                ask_ids.append(ask_id)
        if not ask_ids:
            return None

        mapping = open_id_by_user_id
        if mapping is None:
            mapping = self._resolve_feishu_open_ids(session, ask_ids)
        open_ids: List[str] = []
        for ask_id in ask_ids:
            open_id = mapping.get(ask_id)
            if open_id and open_id not in open_ids:
                open_ids.append(open_id)
            elif not open_id:
                logger.warning(
                    f"记录 ID {record.id}：协同参与人 ask_id={ask_id} 未解析到飞书 open_id"
                )
        return open_ids or None

    @staticmethod
    def _collect_wywj_user_ids_for_open_id_lookup(
        record: CRMSalesVisitRecord,
    ) -> List[str]:
        """收集单条拜访中需要解析飞书 open_id 的用户标识。"""
        from app.utils.participants_utils import parse_collaborative_participants_list

        ids: List[str] = []
        if record.recorder_id:
            ids.append(str(record.recorder_id))
        for item in parse_collaborative_participants_list(
            record.collaborative_participants
        ):
            if not isinstance(item, dict):
                continue
            ask_id = _str_or_none(item.get("ask_id"))
            if ask_id:
                ids.append(ask_id)
        return ids

    @staticmethod
    def _format_wywj_contact_name(record: CRMSalesVisitRecord) -> Optional[str]:
        """网眼云捷接触对象：仅从 contacts[].name 拼接。"""
        raw_contacts = record.contacts
        if not isinstance(raw_contacts, list) or not raw_contacts:
            return None
        names: List[str] = []
        for item in raw_contacts:
            if not isinstance(item, dict):
                continue
            name = _str_or_none(item.get("name"))
            if name:
                names.append(name)
        return "、".join(names) if names else None

    @staticmethod
    def _is_wywj_writeback_record_id(record_id: Optional[str]) -> bool:
        """仅 ``form_`` / ``link_`` 前缀的业务 record_id 需要网眼云捷回写。"""
        rid = _str_or_none(record_id)
        if not rid:
            return False
        return rid.startswith("form_") or rid.startswith("link_")

    def generate_wywj_visit_requests(
        self, session: Session, visit_records: List[CRMSalesVisitRecord]
    ) -> WywjVisitRecordBatchCreateRequest:
        """
        根据拜访记录生成网眼云捷（飞书）拜访回写请求。

        仅处理 ``record_id`` 以 ``form_`` / ``link_`` 开头的拜访记录。

        字段对齐 ``POST /crm-feishu/wywj/visit-record``：
        - source_record_id：本地 ``record_id``（幂等键）
        - followup_object_id / opportunity_id：飞书行 id（rec...），直接取本地跟进对象/商机 id
        - recorder_id / collaborative_participants：飞书 open_id（ou_...）
        """
        visit_requests = WywjVisitRecordBatchCreateRequest(visit_records=[])

        candidates: List[Tuple[CRMSalesVisitRecord, str, FollowupObject]] = []
        user_ids_for_lookup: List[str] = []
        for record in visit_records:
            source_record_id = _str_or_none(record.record_id)
            if not self._is_wywj_writeback_record_id(source_record_id):
                logger.info(
                    f"记录 ID {record.id}：record_id={record.record_id!r} "
                    f"非 form_/link_ 前缀，跳过网眼云捷回写"
                )
                continue

            followup_obj = resolve_followup_object_from_record(record)
            if not followup_obj:
                logger.warning(
                    f"记录 ID {record.id}：无法解析跟进对象（followup_object/account/partner 均为空），"
                    "跳过网眼云捷回写"
                )
                continue

            candidates.append((record, source_record_id, followup_obj))
            user_ids_for_lookup.extend(
                self._collect_wywj_user_ids_for_open_id_lookup(record)
            )

        open_id_by_user_id = (
            self._resolve_feishu_open_ids(session, user_ids_for_lookup)
            if candidates
            else {}
        )

        for record, source_record_id, followup_obj in candidates:
            followup_record = _str_or_none(
                record.followup_record_zh
                or record.followup_record
                or record.followup_content
            )
            next_steps = _str_or_none(record.next_steps_zh or record.next_steps)
            visit_date = (
                record.visit_communication_date.isoformat()
                if record.visit_communication_date
                else None
            )

            visit_requests.visit_records.append(
                WywjVisitRecordCreateRequest(
                    source_record_id=source_record_id,
                    followup_object_id=followup_obj.object_id,
                    opportunity_id=_str_or_none(record.opportunity_id),
                    recorder_id=self._resolve_wywj_recorder_open_id(
                        session, record, open_id_by_user_id
                    ),
                    collaborative_participants=self._resolve_wywj_collaborative_open_ids(
                        session, record, open_id_by_user_id
                    ),
                    visit_communication_date=visit_date,
                    contact_name=self._format_wywj_contact_name(record),
                    followup_record=followup_record,
                    next_steps=next_steps,
                )
            )

        return visit_requests

    def generate_zhipu_visit_requests(
        self, session: Session, visit_records: List[CRMSalesVisitRecord]
    ) -> ZhipuVisitRecordBatchCreateRequest:
        """根据拜访记录生成智谱跟进记录回写请求。

        字段对齐 ``POST /crm-fxiaoke/zhipu/sale-record``（标准字段名，网关映射纷享字段）：
        - ``followup_record``：跟进内容（≈ CBG ``content``，拼装摘要）
        - ``visit_communication_method``：原样透传（前端已对齐智谱枚举）
        - ``followup_object_id`` / ``opportunity_id`` 至少填一个
        - ``followup_object_type``：非 ``end_customer`` 时传递，默认类型由网关处理
        - ``recorder_id``：``user_profiles.crm_user_id``
        """
        visit_requests = ZhipuVisitRecordBatchCreateRequest(visit_records=[])

        for record in visit_records:
            visit_method = _str_or_none(record.visit_communication_method)
            if not visit_method:
                logger.warning(
                    f"记录 ID {record.id}：visit_communication_method 为空，跳过创建智谱跟进记录"
                )
                continue

            followup_obj = resolve_followup_object_from_record(record)
            followup_object_id = (
                _str_or_none(followup_obj.object_id) if followup_obj else None
            )
            followup_object_type = None
            if followup_obj and followup_obj.object_type != FOLLOWUP_OBJECT_TYPE_END_CUSTOMER:
                followup_object_type = _str_or_none(followup_obj.object_type)
            opportunity_id = _str_or_none(record.opportunity_id)

            if not followup_object_id and not opportunity_id:
                logger.warning(
                    f"记录 ID {record.id}：followup_object_id 和 opportunity_id 均为空，"
                    "跳过创建智谱跟进记录"
                )
                continue

            visit_requests.visit_records.append(
                ZhipuVisitRecordCreateRequest(
                    source_record_id=str(record.record_id or record.id),
                    followup_record=self.generate_visit_summary_content(record),
                    visit_communication_method=visit_method,
                    followup_object_id=followup_object_id,
                    followup_object_type=followup_object_type,
                    opportunity_id=opportunity_id,
                    recorder_id=self._resolve_recorder_crm_user_id(session, record),
                )
            )

        return visit_requests

    def writeback_review_opportunity_updates_to_crm(
        self,
        *,
        session_id: str,
        snapshot_period: str,
        ops: List[Dict[str, Any]],
        writeback_source: str = "review_editable_update",
    ) -> Dict[str, Any]:
        """
        将 review 商机**已修改**的可编辑字段推送至 CRM（与拜访回写分离）。

        仅当 ``CRM_WRITEBACK_REVIEW_ENABLED`` 为 True 时调用网关；**不**读取 ``CRM_WRITEBACK_DEFAULT_MODE``。
        ``writeback_source`` 写入客户端日志；每条 HTTP 仅含 ``id`` 及相对 ``before_editable`` 有变化的字段（见
        ``review_op_to_gateway_update_json``）。
        """
        if not settings.CRM_WRITEBACK_REVIEW_ENABLED:
            return {
                "success": True,
                "skipped": True,
                "message": "CRM_WRITEBACK_REVIEW_ENABLED 为关闭，跳过 CRM review 回写",
                "writeback_count": 0,
            }
        if not ops:
            return {
                "success": True,
                "skipped": True,
                "message": "无回写行，跳过 CRM 回写",
                "writeback_count": 0,
            }

        validated: List[ReviewOpportunityWritebackOp] = []
        for raw in ops:
            try:
                validated.append(ReviewOpportunityWritebackOp.model_validate(raw))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "review opportunity writeback: skip invalid op session_id=%s err=%s raw_keys=%s",
                    session_id,
                    e,
                    list(raw.keys()) if isinstance(raw, dict) else type(raw),
                )

        if not validated:
            logger.error(
                "review opportunity writeback: no valid ops session_id=%s source=%s ops_len=%s",
                session_id,
                writeback_source,
                len(ops),
            )
            return {
                "success": False,
                "message": "本批 ops 无法解析为 review 回写请求体，未调用网关",
                "writeback_count": 0,
            }

        batch = ReviewOpportunityWritebackBatchRequest(
            session_id=session_id,
            snapshot_period=snapshot_period,
            ops=validated,
            partial_fail=True,
            source=writeback_source,
        )
        result = self._review_writeback_client.post_review_opportunity_updates(batch)
        posted = int(result.get("posted_count") or 0)
        out: Dict[str, Any] = {
            "success": bool(result.get("success")),
            "writeback_count": posted,
            "message": "CRM review 回写完成" if result.get("success") else result.get("message", "失败"),
        }
        if result.get("data") is not None:
            out["data"] = result.get("data")
        if result.get("response_text") is not None:
            out["response_text"] = result.get("response_text")
        if result.get("gateway_response") is not None:
            out["gateway_response"] = result.get("gateway_response")
        return out

    def _execute_visit_writeback(
        self, session: Session, visit_records: List[CRMSalesVisitRecord], writeback_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        拜访记录回写：按 ``writeback_mode`` 分发；未传时使用 ``CRM_WRITEBACK_DEFAULT_MODE``（若为 ``None`` 则跳过）。

        与 ``writeback_review_opportunity_updates_to_crm`` 及 ``CRM_WRITEBACK_REVIEW_ENABLED`` 无关。
        """
        # 拜访路径：仅使用 CRM_WRITEBACK_DEFAULT_MODE（与 review 的 CRM_WRITEBACK_REVIEW_ENABLED 分离）
        if writeback_mode is None:
            dm = settings.CRM_WRITEBACK_DEFAULT_MODE
            if dm is None:
                logger.info("未配置 CRM_WRITEBACK_DEFAULT_MODE，跳过拜访记录回写")
                return {
                    "success": True,
                    "skipped": True,
                    "message": "未配置 CRM_WRITEBACK_DEFAULT_MODE，跳过拜访记录回写",
                    "processed_count": len(visit_records),
                    "writeback_count": 0,
                }
            writeback_mode = dm.value
        
        # 验证回写模式
        valid_modes = [mode.value for mode in WritebackMode]
        if writeback_mode not in valid_modes:
            logger.error(f"无效的回写模式: {writeback_mode}，支持的模式: {valid_modes}")
            return {
                "success": False,
                "message": f"无效的回写模式: {writeback_mode}，支持的模式: {valid_modes}",
                "processed_count": 0,
                "writeback_count": 0
            }
        
        if not visit_records:
            logger.info("没有需要回写的拜访记录")
            return {
                "success": True,
                "message": "没有需要回写的拜访记录",
                "processed_count": 0,
                "writeback_count": 0
            }
        
        logger.info(f"开始回写 {len(visit_records)} 条拜访记录，回写模式: {writeback_mode}")

        try:
            if writeback_mode == WritebackMode.APAC.value:
                # 任务创建模式
                logger.info("使用APAC任务创建模式")
                task_requests = self.generate_task_requests(visit_records)
                
                if not task_requests:
                    logger.info("没有需要创建任务的拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要创建任务的拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0
                    }
                
                # 创建批量任务请求
                task_batch_request = TaskBatchCreateRequest(
                    tasks=task_requests,
                    partial_fail=True
                )
                
                # 执行批量任务创建
                result = self.client.batch_task_create(task_batch_request)
                
                logger.info(f"批量任务创建完成: {len(task_requests)} 个任务")
                
                return_data = result.get("data", {})
                created_tasks = return_data.get("created", [])
                failed_tasks = return_data.get("failed", [])
                return {
                    "success": result.get("success", False),
                    "message": f"成功处理 {len(visit_records)} 条拜访记录，创建 {len(task_requests)} 个任务",
                    "processed_count": len(visit_records),
                    "writeback_count": len(task_requests),
                    "success_count": len(created_tasks),
                    "failed_count": len(failed_tasks),
                    "results": return_data
                }
            
            elif writeback_mode == WritebackMode.CBG.value:
                # CBG日常对象回写模式
                logger.info("使用CBG日常对象回写模式")
                
                # 生成CBG日常对象创建请求列表
                visit_requests = self.generate_cbg_visit_requests(session, visit_records)
                
                if not visit_requests:
                    logger.info("没有需要创建CBG日常对象的拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要创建CBG日常对象的拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0
                    }
                
                # 执行批量CBG日常对象创建
                result = self.client.batch_cbg_visit_create(visit_requests)
                
                logger.info(f"批量CBG日常对象创建完成: {len(visit_requests.records)} 条记录")
                
                return_data = result.get("data", {})
                created_visits = return_data.get("created", 0)
                failed_visits = return_data.get("failed", 0)
                return {
                    "success": result.get("success", False),
                    "message": f"成功处理 {len(visit_records)} 条拜访记录，创建 {len(visit_requests.records)} 个CBG日常对象",
                    "processed_count": len(visit_records),
                    "writeback_count": len(visit_requests.records),
                    "success_count": created_visits,
                    "failed_count": failed_visits,
                    "results": return_data
                }
            
            elif writeback_mode == WritebackMode.OLM.value:
                # OLM拜访记录回写模式
                logger.info("使用OLM拜访记录回写模式")
                
                # 生成OLM拜访记录请求
                visit_requests = self.generate_olm_visit_requests(session, visit_records)
                
                if not visit_requests:
                    logger.info("没有需要回写的OLM拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要回写的OLM拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0
                    }
                
                # 执行批量OLM拜访记录创建
                result = self.client.batch_olm_visit_create(visit_requests)
                
                logger.info(f"批量OLM拜访记录创建完成: {len(visit_requests.visit_records)} 条记录")
                
                return_data = result.get("data", {})
                if isinstance(return_data, dict):
                    created_visits = return_data.get("created", 0)
                    failed_visits = return_data.get("failed", 0)
                    return {
                        "success": result.get("success", False),
                        "message": f"成功处理 {len(visit_records)} 条拜访记录，回写 {len(visit_requests.visit_records)} 条OLM记录",
                        "processed_count": len(visit_records),
                        "writeback_count": len(visit_requests.visit_records),
                        "success_count": created_visits,
                        "failed_count": failed_visits,
                        "results": return_data
                    }
                else:
                    # 如果返回格式不是预期的字典格式
                    return {
                        "success": result.get("success", False),
                        "message": f"成功处理 {len(visit_records)} 条拜访记录，回写 {len(visit_requests.visit_records)} 条OLM记录",
                        "processed_count": len(visit_records),
                        "writeback_count": len(visit_requests.visit_records),
                        "results": return_data
                    }
            
            elif writeback_mode == WritebackMode.CHAITIN.value:
                # CHAITIN拜访记录回写模式
                logger.info("使用CHAITIN拜访记录回写模式")
                
                # 生成CHAITIN拜访记录请求
                visit_requests = self.generate_chaitin_visit_requests(session, visit_records)
                
                if not visit_requests:
                    logger.info("没有需要回写的CHAITIN拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要回写的CHAITIN拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0
                    }
                
                # 执行批量CHAITIN拜访记录创建
                result = self.client.batch_chaitin_visit_create(visit_requests)
                
                logger.info(f"批量CHAITIN拜访记录创建完成: {len(visit_requests.followup_records)} 条记录")
                
                return_data = result.get("data", {})
                if isinstance(return_data, dict):
                    created_visits = return_data.get("created", 0)
                    failed_visits = return_data.get("failed", 0)
                    return {
                        "success": result.get("success", False),
                        "message": f"成功处理 {len(visit_records)} 条拜访记录，回写 {len(visit_requests.followup_records)} 条CHAITIN记录",
                        "processed_count": len(visit_records),
                        "writeback_count": len(visit_requests.followup_records),
                        "success_count": created_visits,
                        "failed_count": failed_visits,
                        "results": return_data
                    }
                else:
                    # 如果返回格式不是预期的字典格式
                    return {
                        "success": result.get("success", False),
                        "message": f"成功处理 {len(visit_records)} 条拜访记录，回写 {len(visit_requests.followup_records)} 条CHAITIN记录",
                        "processed_count": len(visit_records),
                        "writeback_count": len(visit_requests.followup_records),
                        "results": return_data
                    }
            
            elif writeback_mode == WritebackMode.WEBEYE.value:
                logger.info("使用WEBEYE（简道云）拜访记录回写模式")

                visit_requests = self.generate_webeye_visit_requests(session, visit_records)

                if not visit_requests.visit_records:
                    logger.info("没有需要回写的WEBEYE拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要回写的WEBEYE拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0,
                    }

                result = self.client.batch_webeye_visit_create(visit_requests)
                logger.info(
                    f"批量WEBEYE拜访记录回写完成: {len(visit_requests.visit_records)} 条记录"
                )

                return_data = result.get("data", {})
                writeback_count = len(visit_requests.visit_records)
                ok = bool(result.get("success"))
                if isinstance(return_data, dict):
                    created_visits = return_data.get("created", 0)
                    updated_visits = return_data.get("updated", 0)
                    failed_visits = return_data.get("failed", 0)
                    return {
                        "success": ok,
                        "message": (
                            f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                            f"提交 {writeback_count} 条WEBEYE回写"
                            + (
                                f"（created={created_visits}, updated={updated_visits}, "
                                f"failed={failed_visits}）"
                                if ok
                                else f"：{result.get('message', '')}"
                            )
                        ),
                        "processed_count": len(visit_records),
                        "writeback_count": writeback_count,
                        "success_count": created_visits + updated_visits if ok else 0,
                        "failed_count": failed_visits if ok else writeback_count,
                        "results": return_data,
                    }
                return {
                    "success": ok,
                    "message": (
                        f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                        f"提交 {writeback_count} 条WEBEYE回写"
                        + ("" if ok else f"：{result.get('message', '')}")
                    ),
                    "processed_count": len(visit_records),
                    "writeback_count": writeback_count,
                    "results": return_data,
                }

            elif writeback_mode == WritebackMode.FENBEITONG.value:
                logger.info("使用分贝通跟进记录回写模式")

                visit_requests = self.generate_fenbeitong_visit_requests(
                    session, visit_records
                )

                if not visit_requests.records:
                    logger.info("没有需要创建分贝通跟进记录的拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要创建分贝通跟进记录的拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0,
                    }

                result = self.client.batch_fenbeitong_visit_create(visit_requests)
                logger.info(
                    f"批量分贝通跟进记录创建完成: {len(visit_requests.records)} 条记录"
                )

                return_data = result.get("data", {})
                writeback_count = len(visit_requests.records)
                ok = bool(result.get("success"))
                if isinstance(return_data, dict):
                    created_visits = return_data.get("created", 0)
                    updated_visits = return_data.get("updated", 0)
                    failed_visits = return_data.get("failed", 0)
                    return {
                        "success": ok,
                        "message": (
                            f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                            f"提交 {writeback_count} 条分贝通回写"
                            + (
                                f"（created={created_visits}, updated={updated_visits}, "
                                f"failed={failed_visits}）"
                                if ok
                                else f"：{result.get('message', '')}"
                            )
                        ),
                        "processed_count": len(visit_records),
                        "writeback_count": writeback_count,
                        "success_count": (
                            created_visits + updated_visits if ok else 0
                        ),
                        "failed_count": failed_visits if ok else writeback_count,
                        "results": return_data,
                    }
                return {
                    "success": ok,
                    "message": (
                        f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                        f"提交 {writeback_count} 条分贝通回写"
                        + ("" if ok else f"：{result.get('message', '')}")
                    ),
                    "processed_count": len(visit_records),
                    "writeback_count": writeback_count,
                    "results": return_data,
                }

            elif writeback_mode == WritebackMode.LIEPIN.value:
                logger.info("使用猎聘拜访记录回写模式")

                visit_requests = self.generate_liepin_visit_requests(
                    session, visit_records
                )

                if not visit_requests.visits:
                    logger.info("没有需要回写的猎聘拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要回写的猎聘拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0,
                    }

                result = self.client.batch_liepin_visit_create(visit_requests)
                logger.info(
                    f"批量猎聘拜访记录回写完成: {len(visit_requests.visits)} 条记录"
                )

                return_data = result.get("data", {})
                writeback_count = len(visit_requests.visits)
                ok = bool(result.get("success"))
                if isinstance(return_data, dict):
                    # 网关批量响应：success / failed 计数
                    success_count = return_data.get("success", 0)
                    failed_count = return_data.get("failed", 0)
                    return {
                        "success": ok,
                        "message": (
                            f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                            f"提交 {writeback_count} 条猎聘回写"
                            + (
                                f"（success={success_count}, failed={failed_count}）"
                                if ok
                                else f"：{result.get('message', '')}"
                            )
                        ),
                        "processed_count": len(visit_records),
                        "writeback_count": writeback_count,
                        "success_count": success_count if ok else 0,
                        "failed_count": failed_count if ok else writeback_count,
                        "results": return_data,
                    }
                return {
                    "success": ok,
                    "message": (
                        f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                        f"提交 {writeback_count} 条猎聘回写"
                        + ("" if ok else f"：{result.get('message', '')}")
                    ),
                    "processed_count": len(visit_records),
                    "writeback_count": writeback_count,
                    "results": return_data,
                }

            elif writeback_mode == WritebackMode.WYWJ.value:
                logger.info("使用网眼云捷（飞书）拜访记录回写模式")

                visit_requests = self.generate_wywj_visit_requests(
                    session, visit_records
                )

                if not visit_requests.visit_records:
                    logger.info("没有需要回写的网眼云捷拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要回写的网眼云捷拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0,
                    }

                result = self.client.batch_wywj_visit_create(visit_requests)
                logger.info(
                    f"批量网眼云捷拜访记录回写完成: {len(visit_requests.visit_records)} 条记录"
                )

                return_data = result.get("data", {})
                writeback_count = len(visit_requests.visit_records)
                ok = bool(result.get("success"))
                if isinstance(return_data, dict):
                    created_visits = return_data.get("created", 0)
                    updated_visits = return_data.get("updated", 0)
                    failed_visits = return_data.get("failed", 0)
                    return {
                        "success": ok,
                        "message": (
                            f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                            f"提交 {writeback_count} 条网眼云捷回写"
                            + (
                                f"（created={created_visits}, updated={updated_visits}, "
                                f"failed={failed_visits}）"
                                if ok
                                else f"：{result.get('message', '')}"
                            )
                        ),
                        "processed_count": len(visit_records),
                        "writeback_count": writeback_count,
                        "success_count": (
                            created_visits + updated_visits if ok else 0
                        ),
                        "failed_count": failed_visits if ok else writeback_count,
                        "results": return_data,
                    }
                return {
                    "success": ok,
                    "message": (
                        f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                        f"提交 {writeback_count} 条网眼云捷回写"
                        + ("" if ok else f"：{result.get('message', '')}")
                    ),
                    "processed_count": len(visit_records),
                    "writeback_count": writeback_count,
                    "results": return_data,
                }

            elif writeback_mode == WritebackMode.ZHIPU.value:
                logger.info("使用智谱跟进记录回写模式")

                visit_requests = self.generate_zhipu_visit_requests(
                    session, visit_records
                )

                if not visit_requests.visit_records:
                    logger.info("没有需要创建智谱跟进记录的拜访记录")
                    return {
                        "success": True,
                        "message": "没有需要创建智谱跟进记录的拜访记录",
                        "processed_count": len(visit_records),
                        "writeback_count": 0,
                    }

                result = self.client.batch_zhipu_visit_create(visit_requests)
                logger.info(
                    f"批量智谱跟进记录回写完成: {len(visit_requests.visit_records)} 条记录"
                )

                return_data = result.get("data", {})
                writeback_count = len(visit_requests.visit_records)
                ok = bool(result.get("success"))
                if isinstance(return_data, dict):
                    created_visits = return_data.get("created", 0)
                    updated_visits = return_data.get("updated", 0)
                    failed_visits = return_data.get("failed", 0)
                    return {
                        "success": ok,
                        "message": (
                            f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                            f"提交 {writeback_count} 条智谱回写"
                            + (
                                f"（created={created_visits}, updated={updated_visits}, "
                                f"failed={failed_visits}）"
                                if ok
                                else f"：{result.get('message', '')}"
                            )
                        ),
                        "processed_count": len(visit_records),
                        "writeback_count": writeback_count,
                        "success_count": (
                            created_visits + updated_visits if ok else 0
                        ),
                        "failed_count": failed_visits if ok else writeback_count,
                        "results": return_data,
                    }
                return {
                    "success": ok,
                    "message": (
                        f"{'成功' if ok else '失败'}处理 {len(visit_records)} 条拜访记录，"
                        f"提交 {writeback_count} 条智谱回写"
                        + ("" if ok else f"：{result.get('message', '')}")
                    ),
                    "processed_count": len(visit_records),
                    "writeback_count": writeback_count,
                    "results": return_data,
                }

            else:
                logger.warning(
                    "拜访记录回写尚未实现: writeback_mode=%s visit_rows=%s",
                    writeback_mode,
                    len(visit_records),
                )
                return {
                    "success": True,
                    "message": (
                        f"WritebackMode={writeback_mode} 在本服务未实现拜访记录回写；"
                        "review 商机回写由 CRM_WRITEBACK_REVIEW_ENABLED 单独控制"
                    ),
                    "processed_count": len(visit_records),
                    "writeback_count": 0,
                    "writeback_skipped_reason": "visit_writeback_not_implemented_for_mode",
                }
        except Exception as e:
            logger.exception(f"回写拜访记录失败: {e}")
            return {
                "success": False,
                "message": f"回写失败: {str(e)}",
                "processed_count": 0,
                "writeback_count": 0
            }
    
    def writeback_visit_records_by_ids(self, session: Session, visit_record_ids: List[int], 
                                     writeback_mode: Optional[str] = None) -> Dict[str, Any]:
        """
        根据ID列表回写指定的拜访记录
        
        Args:
            session: 数据库会话
            visit_record_ids: 拜访记录ID列表
            writeback_mode: 网关变体字符串；不传则使用 ``CRM_WRITEBACK_DEFAULT_MODE``；二者均为空时跳过回写
        
        Returns:
            回写结果，包含找到的记录ID和缺失的记录ID
        """
        try:
            stmt = select(CRMSalesVisitRecord).where(
                CRMSalesVisitRecord.id.in_(visit_record_ids),
            )
            visit_records = session.exec(stmt).all()
            
            if not visit_records:
                logger.info(f"未找到指定的拜访记录，请求的ID: {visit_record_ids}")
                return {
                    "success": False,
                    "message": "未找到指定的拜访记录",
                    "processed_count": 0,
                    "writeback_count": 0,
                    "requested_ids": visit_record_ids,
                    "found_ids": [],
                    "missing_ids": visit_record_ids
                }
            
            found_ids = [record.id for record in visit_records]
            missing_ids = [id for id in visit_record_ids if id not in found_ids]
            
            logger.info(f"找到 {len(visit_records)} 条拜访记录，缺失 {len(missing_ids)} 条")
            
            # 执行回写
            result = self._execute_visit_writeback(session, visit_records, writeback_mode)
            
            # 添加ID信息到结果中
            result["requested_ids"] = visit_record_ids
            result["found_ids"] = found_ids
            result["missing_ids"] = missing_ids
            
            return result
            
        except Exception as e:
            logger.exception(f"根据ID回写拜访记录失败: {e}")
            return {
                "success": False,
                "message": f"回写失败: {str(e)}",
                "processed_count": 0,
                "writeback_count": 0,
                "requested_ids": visit_record_ids,
                "found_ids": [],
                "missing_ids": []
            }
    
    def writeback_visit_records(self, session: Session, start_datetime: datetime, 
                               end_datetime: datetime, writeback_mode: Optional[str] = None) -> Dict[str, Any]:
        """
        回写指定时间范围内的拜访记录（根据录入时间筛选）
        
        Args:
            session: 数据库会话
            start_datetime: 开始时间（UTC时间）
            end_datetime: 结束时间（UTC时间）
            writeback_mode: 网关变体字符串；不传则使用 ``CRM_WRITEBACK_DEFAULT_MODE``；二者均为空时跳过回写
        
        Returns:
            回写结果
        """
        try:
            # 查询指定时间范围内的拜访记录（使用last_modified_time，即录入时间，UTC时间）
            stmt = select(CRMSalesVisitRecord).where(
                CRMSalesVisitRecord.last_modified_time >= start_datetime,
                CRMSalesVisitRecord.last_modified_time <= end_datetime
            ).order_by(CRMSalesVisitRecord.last_modified_time)
            
            visit_records = session.exec(stmt).all()
            
            if not visit_records:
                logger.info(f"在 {start_datetime} 到 {end_datetime} 时间范围内（UTC）没有找到拜访记录")
                return {
                    "success": True,
                    "message": f"在 {start_datetime} 到 {end_datetime} 时间范围内（UTC）没有找到拜访记录",
                    "processed_count": 0,
                    "writeback_count": 0
                }
            
            logger.info(f"找到 {len(visit_records)} 条拜访记录，开始进行回写")
            
            # 复用核心回写逻辑
            return self._execute_visit_writeback(session, visit_records, writeback_mode)
            
        except Exception as e:
            logger.exception(f"回写拜访记录失败: {e}")
            return {
                "success": False,
                "message": f"回写失败: {str(e)}",
                "processed_count": 0,
                "writeback_count": 0
            }


# 创建全局服务实例
crm_writeback_service = CrmWritebackService()

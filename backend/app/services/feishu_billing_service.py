import logging
import hashlib
import time
from typing import Any, Optional
from uuid import uuid4

import requests
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine

logger = logging.getLogger(__name__)

_PERSIST_MSG_MAX = 16000
_PERSIST_REVIEW_DETAIL_MAX = 32000

# 拜访记录计费功能：行为数据采集与质检
def _truncate_text(value: Optional[str], max_len: int) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _persist_usage_report_outcome(
    *,
    trace_id: str,
    ai_module_key: str,
    operator: str,
    review_detail: str,
    status: str,
    last_api_code: Optional[int],
    last_message: Optional[str],
    attempts: int,
    last_exception_type: Optional[str],
) -> None:
    """将 pending_insert 最终结果写入 feishu_billing_usage_report；失败不影响主流程。"""
    from app.models.feishu_billing_usage_report import FeishuBillingUsageReport

    try:
        with Session(engine, expire_on_commit=False) as session:
            row = session.exec(
                select(FeishuBillingUsageReport).where(FeishuBillingUsageReport.trace_id == trace_id)
            ).first()
            rd = _truncate_text(review_detail, _PERSIST_REVIEW_DETAIL_MAX)
            msg = _truncate_text(last_message, _PERSIST_MSG_MAX)
            if row:
                row.ai_module_key = ai_module_key
                row.operator = operator
                row.review_detail = rd
                row.status = status
                row.last_api_code = last_api_code
                row.last_message = msg
                row.attempts = attempts
                row.last_exception_type = last_exception_type
            else:
                session.add(
                    FeishuBillingUsageReport(
                        trace_id=trace_id,
                        ai_module_key=ai_module_key,
                        operator=operator,
                        review_detail=rd,
                        status=status,
                        last_api_code=last_api_code,
                        last_message=msg,
                        attempts=attempts,
                        last_exception_type=last_exception_type,
                    )
                )
            session.commit()
    except Exception as exc:
        logger.error(
            "Failed to persist billing usage report outcome trace_id=%s status=%s: %s",
            trace_id,
            status,
            exc,
            exc_info=True,
        )


VISIT_RECORD_AI_MODULE_KEY = "behavior_data_collection_qa"
SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY = "sales_personal_daily_report"
SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY = "sales_team_daily_report"
TEAM_WEEKLY_REPORT_AI_MODULE_KEY = "team_weekly_report"
WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY = "weekly_followup_summary"
ACCOUNT_VISIT_PREP_GUIDE_AI_MODULE_KEY = "account_visit_prep_guide"
SIA_AI_INTERACTION_AI_MODULE_KEY = "sia_ai_interaction"


class FeishuBillingService:
    def __init__(self) -> None:
        self._base_url = settings.FEISHU_PAID_API_BASE_URL.rstrip("/")
        self._session = requests.Session()
        self._timeout_seconds = settings.FEISHU_PAID_API_TIMEOUT_SECONDS
        self._retry_attempts = max(1, settings.FEISHU_PAID_API_RETRY_ATTEMPTS)
        self._retry_base_seconds = max(0.1, settings.FEISHU_PAID_API_RETRY_BASE_SECONDS)

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        response = self._session.get(
            f"{self._base_url}{path}",
            params=params or None,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid billing response: {data}")
        return data

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._session.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=self._timeout_seconds,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid billing response: {data}")
        return data

    def check_quota(self, ai_module_key: Optional[str] = None) -> tuple[bool, str, int]:
        """
        查询租户 AI 额度。

        传入 ``ai_module_key`` 时按该功能计费点数校验 remaining quota；
        响应可能含 ``required_points``，额度不足时 ``msg`` 会带具体点数说明。
        """
        params: Optional[dict[str, Any]] = None
        if ai_module_key:
            params = {"ai_module_key": ai_module_key}
        data = self._get("/v1/usage_records/tenant_quota", params=params)
        code = data.get("code")
        api_msg = str(data.get("msg") or "").strip()
        if code == 400:
            return False, api_msg or f"计费配置无效(code={code})", 0
        if code == 502:
            return False, api_msg or f"查询租户 AI 额度失败(code={code})", 0
        if code != 200:
            return False, api_msg or f"计费额度查询失败(code={code})", 0

        tenant_quota = (data.get("data") or {}).get("tenant_quota") or {}
        sufficient = tenant_quota.get("sufficient")
        quota = int(tenant_quota.get("quota") or 0)
        if sufficient is not True:
            return False, api_msg or "租户额度不足，请联系管理员", quota
        if quota <= 0:
            return False, api_msg or "租户额度不足，请联系管理员", quota
        return True, api_msg or "租户额度充足", quota

    @staticmethod
    def normalize_operator(raw_user_id: Any) -> str:
        user_id = str(raw_user_id or "").strip()
        if not user_id:
            return "system"
        normalized = user_id.replace("-", "")
        return normalized or "system"

    @staticmethod
    def new_trace_id(prefix: str = "visit-record") -> str:
        return f"{prefix}-{uuid4()}"

    @staticmethod
    def deterministic_trace_id(prefix: str, unique_key: str) -> str:
        digest = hashlib.sha256(unique_key.encode("utf-8")).hexdigest()[:24]
        return f"{prefix}-{digest}"

    def report_usage_with_retry(
        self,
        *,
        trace_id: str,
        operator: str,
        review_detail: str,
        ai_module_key: str = VISIT_RECORD_AI_MODULE_KEY,
    ) -> tuple[bool, int, str]:
        payload = {
            "trace_id": trace_id,
            "operator": operator,
            "review_detail": review_detail,
            "ai_module_key": ai_module_key,
        }
        last_code = -1
        last_msg = "unknown"
        last_exc_type: Optional[str] = None
        attempts_used = 0
        for attempt in range(self._retry_attempts):
            attempts_used = attempt + 1
            try:
                data = self._post("/v1/usage_records/pending_insert", payload)
                last_exc_type = None
                code = int(data.get("code") or 0)
                msg = str(data.get("msg") or "")
                if code in (200, 409):
                    _persist_usage_report_outcome(
                        trace_id=trace_id,
                        ai_module_key=ai_module_key,
                        operator=operator,
                        review_detail=review_detail,
                        status="success",
                        last_api_code=code,
                        last_message=msg,
                        attempts=attempts_used,
                        last_exception_type=None,
                    )
                    return True, code, msg
                last_code = code
                last_msg = msg
            except Exception as exc:
                last_code = -1
                last_msg = str(exc)
                last_exc_type = type(exc).__name__
                logger.warning(
                    "Billing usage report attempt failed, trace_id=%s attempt=%s/%s error=%s",
                    trace_id,
                    attempt + 1,
                    self._retry_attempts,
                    exc,
                )
            if attempt < self._retry_attempts - 1:
                time.sleep(self._retry_base_seconds * (2 ** attempt))
        persist_code: Optional[int] = None if last_code < 0 else last_code
        _persist_usage_report_outcome(
            trace_id=trace_id,
            ai_module_key=ai_module_key,
            operator=operator,
            review_detail=review_detail,
            status="failed",
            last_api_code=persist_code,
            last_message=last_msg,
            attempts=attempts_used,
            last_exception_type=last_exc_type,
        )
        return False, last_code, last_msg


feishu_billing_service = FeishuBillingService()

import logging
import hashlib
import time
from typing import Any
from uuid import uuid4

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# 拜访记录计费功能：行为数据采集与质检
VISIT_RECORD_AI_MODULE_KEY = "behavior_data_collection_qa"
SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY = "sales_personal_daily_report"
SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY = "sales_team_daily_report"
TEAM_WEEKLY_REPORT_AI_MODULE_KEY = "team_weekly_report"
WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY = "weekly_followup_summary"
SIA_AI_INTERACTION_AI_MODULE_KEY = "sia_ai_interaction"


class FeishuBillingService:
    def __init__(self) -> None:
        self._base_url = settings.FEISHU_PAID_API_BASE_URL.rstrip("/")
        self._session = requests.Session()
        self._timeout_seconds = settings.FEISHU_PAID_API_TIMEOUT_SECONDS
        self._retry_attempts = max(1, settings.FEISHU_PAID_API_RETRY_ATTEMPTS)
        self._retry_base_seconds = max(0.1, settings.FEISHU_PAID_API_RETRY_BASE_SECONDS)

    def _get(self, path: str) -> dict[str, Any]:
        response = self._session.get(
            f"{self._base_url}{path}",
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

    def check_quota(self) -> tuple[bool, str, int]:
        data = self._get("/v1/usage_records/tenant_quota")
        code = data.get("code")
        if code != 200:
            return False, f"计费额度查询失败(code={code})", 0

        tenant_quota = (data.get("data") or {}).get("tenant_quota") or {}
        sufficient = tenant_quota.get("sufficient")
        quota = int(tenant_quota.get("quota") or 0)
        if sufficient is not True:
            return False, "租户额度不足，请联系管理员", quota
        if quota <= 0:
            return False, "租户额度不足，请联系管理员", quota
        return True, "租户额度充足", quota

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
        for attempt in range(self._retry_attempts):
            try:
                data = self._post("/v1/usage_records/pending_insert", payload)
                code = int(data.get("code") or 0)
                msg = str(data.get("msg") or "")
                if code in (200, 409):
                    return True, code, msg
                last_code = code
                last_msg = msg
            except Exception as exc:
                last_code = -1
                last_msg = str(exc)
                logger.warning(
                    "Billing usage report attempt failed, trace_id=%s attempt=%s/%s error=%s",
                    trace_id,
                    attempt + 1,
                    self._retry_attempts,
                    exc,
                )
            if attempt < self._retry_attempts - 1:
                time.sleep(self._retry_base_seconds * (2 ** attempt))
        return False, last_code, last_msg


feishu_billing_service = FeishuBillingService()

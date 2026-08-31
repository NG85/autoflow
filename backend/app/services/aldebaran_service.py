import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_path(path: str, *, default: str) -> str:
    try:
        if not path:
            return default
        if not path.startswith("/"):
            path = f"/{path}"
        return path
    except Exception:
        return default


def _json_safe(value: Any) -> Any:
    """将 payload 转为可 JSON 序列化的结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


@dataclass(frozen=True)
class AldebaranOpportunityForecast:
    opportunity_id: str
    forecast_amount: Optional[float] = None
    expected_closing_date: Optional[date] = None


def parse_aldebaran_closing_date(raw: Any) -> Optional[date]:
    """将 Aldebaran/CRM 预计成交日期规范为 date（仅接受 YYYY-MM-DD）。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def normalize_opportunity_forecast_batch_response(body: Any) -> dict[str, AldebaranOpportunityForecast]:
    """
    将 Aldebaran ``POST /api/v1/opportunity/query/amount`` 响应归一化为 opportunity_id -> forecast 映射。

    响应示例::

        {
          "status": "success",
          "data": {
            "opportunities": [
              {
                "unique_id": "...",
                "forecast_amount": 390000.0,
                "expected_closing_date": "2026-03-04",
                "amount_field": "estimated_acv"
              }
            ]
          }
        }
    """
    if not isinstance(body, dict):
        return {}

    data = body.get("data")
    if not isinstance(data, dict):
        return {}

    opportunities = data.get("opportunities")
    if not isinstance(opportunities, list):
        return {}

    out: dict[str, AldebaranOpportunityForecast] = {}
    for item in opportunities:
        if not isinstance(item, dict):
            continue
        unique_id = str(item.get("unique_id") or "").strip()
        if not unique_id:
            continue

        forecast_amount: Optional[float] = None
        raw_amount = item.get("forecast_amount")
        if raw_amount is not None:
            try:
                forecast_amount = float(raw_amount)
            except (TypeError, ValueError):
                forecast_amount = None

        out[unique_id] = AldebaranOpportunityForecast(
            opportunity_id=unique_id,
            forecast_amount=forecast_amount,
            expected_closing_date=parse_aldebaran_closing_date(item.get("expected_closing_date")),
        )
    return out


class AldebaranClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session: Optional[requests.Session] = None,
        weekly_report_path: Optional[str] = None,
        cvgg_path: Optional[str] = None,
        review_session_recalc_path: Optional[str] = None,
        opportunity_query_amount_path: Optional[str] = None,
        messages_incoming_path: Optional[str] = None,
        message_webhook_secret: Optional[str] = None,
    ) -> None:
        self._base_url = (base_url or settings.ALDEBARAN_BASE_URL).rstrip("/")
        self._tenant_id = tenant_id or getattr(settings, "ALDEBARAN_TENANT_ID", "PINGCAP")
        self._session = session or requests.Session()
        self._weekly_report_path = _normalize_path(
            weekly_report_path or getattr(settings, "ALDEBARAN_WEEKLY_REPORT_URL", "/api/v1/report/weekly"),
            default="/api/v1/report/weekly",
        )
        self._cvgg_path = _normalize_path(
            cvgg_path or getattr(settings, "ALDEBARAN_CVGG_URL", "/api/v1/previsit/create_v4"),
            default="/api/v1/previsit/create_v4",
        )
        self._review_session_recalc_path = _normalize_path(
            review_session_recalc_path or getattr(settings, "ALDEBARAN_REVIEW_SESSION_RECALC_PATH", "/api/v1/review/performance/query"),
            default="/api/v1/review/performance/query",
        )
        self._opportunity_query_amount_path = _normalize_path(
            opportunity_query_amount_path
            or getattr(
                settings,
                "ALDEBARAN_OPPORTUNITY_QUERY_AMOUNT_PATH",
                "/api/v1/opportunity/query/amount",
            ),
            default="/api/v1/opportunity/query/amount",
        )
        self._messages_incoming_path = _normalize_path(
            messages_incoming_path or getattr(settings, "ALDEBARAN_MESSAGES_INCOMING_PATH", "/api/v1/messages/incoming"),
            default="/api/v1/messages/incoming",
        )
        self._message_webhook_secret = (
            message_webhook_secret
            if message_webhook_secret is not None
            else getattr(settings, "ALDEBARAN_MESSAGE_WEBHOOK_SECRET", "") or ""
        )

    def _message_request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._message_webhook_secret:
            headers["X-Aldebaran-Message-Secret"] = self._message_webhook_secret
        return headers

    def _post_json_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> requests.Response:
        attempts = max(1, int(getattr(settings, "ALDEBARAN_MESSAGE_RETRY_ATTEMPTS", 3) or 1))
        base_sleep = float(getattr(settings, "ALDEBARAN_MESSAGE_RETRY_BASE_SECONDS", 0.5) or 0.5)
        last_exc: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.post(
                    url,
                    json=payload,
                    headers=self._message_request_headers(),
                    timeout=timeout_seconds,
                )
                if resp.status_code >= 500 and attempt < attempts:
                    logger.warning(
                        "Aldebaran message request 5xx, retry %s/%s: %s %s",
                        attempt,
                        attempts,
                        resp.status_code,
                        url,
                    )
                    time.sleep(base_sleep * attempt)
                    continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < attempts:
                    logger.warning(
                        "Aldebaran message request error, retry %s/%s: %s",
                        attempt,
                        attempts,
                        exc,
                    )
                    time.sleep(base_sleep * attempt)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Aldebaran message request failed without response")

    def submit_incoming_message(
        self,
        *,
        message_type: str,
        source_unique_id: str,
        payload: Optional[dict[str, Any]] = None,
        source_system: Optional[str] = None,
        source_table: Optional[str] = None,
        event_time: Optional[datetime] = None,
        dedupe_key: Optional[str] = None,
        trace_id: Optional[str] = None,
        priority: int = 0,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """
        调用 Aldebaran ``POST /api/v1/messages/incoming`` 提交上游事件（幂等）。

        参见 Aldebaran Incoming Message Digest 集成文档（POST /messages/incoming）。
        """
        url = f"{self._base_url}{self._messages_incoming_path}"
        body: dict[str, Any] = {
            "message_type": message_type,
            "source_system": source_system or settings.ALDEBARAN_MESSAGE_SOURCE_SYSTEM,
            "source_unique_id": source_unique_id,
            "priority": int(priority),
        }
        if source_table:
            body["source_table"] = source_table
        if event_time is not None:
            body["event_time"] = event_time.isoformat()
        if dedupe_key:
            body["dedupe_key"] = dedupe_key
        if trace_id:
            body["trace_id"] = trace_id
        if payload is not None:
            body["payload"] = _json_safe(payload)

        logger.info(
            "Submit Aldebaran incoming message: url=%s type=%s source_unique_id=%s dedupe_key=%s",
            url,
            message_type,
            source_unique_id,
            dedupe_key or "(auto)",
        )

        resp = self._post_json_with_retry(url, body, timeout_seconds=timeout_seconds)

        if resp.status_code == 403:
            raise RuntimeError("Aldebaran message rejected: invalid or missing X-Aldebaran-Message-Secret")
        if resp.status_code >= 400:
            detail = resp.text[:500] if resp.text else resp.reason
            raise RuntimeError(
                f"Aldebaran message request failed: HTTP {resp.status_code} {detail}"
            )

        data = resp.json()
        if not isinstance(data, dict) or data.get("status") != "success":
            raise RuntimeError(f"Aldebaran message invalid response: {data}")

        result = data.get("data")
        if not isinstance(result, dict):
            raise RuntimeError(f"Aldebaran message missing data: {data}")

        logger.info(
            "Aldebaran message accepted: message_id=%s dedupe_result=%s status=%s",
            result.get("message_id"),
            result.get("dedupe_result"),
            result.get("status"),
        )
        return result

    def fetch_weekly_report(
        self,
        *,
        report_year: int,
        report_week_of_year: int,
        department_name: Optional[str],
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """
        调用 Aldebaran 周报接口获取周报内容。

        约定：
        - 公司周报：department_name=None
        - 部门周报：department_name=部门名
        """
        url = f"{self._base_url}{self._weekly_report_path}"
        payload = {
            "tenant_id": self._tenant_id,
            "report_year": int(report_year),
            "report_week_of_year": int(report_week_of_year),
            "department": department_name,
        }

        logger.info(
            "调用 Aldebaran 周报接口: %s, payload=%s",
            url,
            {
                "tenant_id": payload["tenant_id"],
                "report_year": payload["report_year"],
                "report_week_of_year": payload["report_week_of_year"],
                "department": payload["department"],
            },
        )

        resp = self._session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        resp.raise_for_status()

        data = resp.json()

        if isinstance(data, dict) and data.get("status") == "success":
            report_data = data.get("data")
            if not isinstance(report_data, dict):
                raise RuntimeError(f"Aldebaran weekly report missing data: {data}")
            result = report_data
        else:
            raise RuntimeError(f"Aldebaran weekly report invalid json: {data}")

        if department_name:
            result.setdefault("department_name", department_name)

        return result

    def generate_client_visit_guide(
        self,
        *,
        account_name: str,
        account_id: str,
        lang: str,
        content: str,
        incoming_cookie: str,
        user_id: str,
        timeout_seconds: int = 300,
    ) -> Any:
        """调用 Aldebaran CVGG 服务生成客户拜访指引。"""
        url = f"{self._base_url}{self._cvgg_path}"
        payload = {
            "account_name": account_name,
            "account_id": account_id,
            "lang": lang,
            "content": content,
            "tenant_id": self._tenant_id,
        }

        resp = self._session.post(
            url,
            json=payload,
            timeout=timeout_seconds,
            headers={"cookie": incoming_cookie, "user_id": user_id},
        )
        resp.raise_for_status()

        result = resp.json()
        if not isinstance(result, dict) or "data" not in result:
            raise RuntimeError(f"Aldebaran cvgg invalid json: {result}")

        return result["data"]

    def trigger_review_session_forecast_recalc(
        self,
        *,
        session_id: str,
        owner_id: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """调用 Aldebaran review performance query。"""
        path = self._review_session_recalc_path
        url = f"{self._base_url}{_normalize_path(path, default='/api/v1/review/performance/query')}"
        payload: dict[str, Any] = {"session_id": session_id}
        if owner_id:
            payload["owner_id"] = owner_id
        logger.info(
            "调用 Aldebaran review performance query: %s, session_id=%s owner_id=%s",
            url,
            session_id,
            owner_id or "(full session)",
        )
        resp = self._session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": data}

    def query_opportunity_amount(
        self,
        opportunity_ids: list[str],
        *,
        timeout_seconds: int = 30,
    ) -> dict[str, AldebaranOpportunityForecast]:
        """
        批量查询商机的 ``forecast_amount`` / ``expected_closing_date``。

        调用 Aldebaran ``POST /api/v1/opportunity/query/amount``：
        - 请求：``{ "opportunity_ids": [...] }``
        - 响应：``data.opportunities[]``，每项含 ``unique_id``、``forecast_amount``、``expected_closing_date``
        """
        normalized_ids = [str(v).strip() for v in opportunity_ids if str(v).strip()]
        if not normalized_ids:
            return {}

        url = f"{self._base_url}{self._opportunity_query_amount_path}"
        payload = {"opportunity_ids": list(dict.fromkeys(normalized_ids))}
        logger.info(
            "调用 Aldebaran 商机金额查询接口: %s, opportunity_count=%s",
            url,
            len(payload["opportunity_ids"]),
        )

        resp = self._session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or data.get("status") != "success":
            raise RuntimeError(f"Aldebaran opportunity query amount invalid response: {data}")
        return normalize_opportunity_forecast_batch_response(data)

    def trigger_visit_record_post_process(
        self,
        *,
        record_id: str,
        event_time: Optional[datetime] = None,
        message_type: Optional[str] = None,
        dedupe_key: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """
        拜访记录事件入队 Aldebaran（POST /api/v1/messages/incoming）。
        创建默认 ``crm.visit_record.saved``；拜访记录修订可传入 ``crm.visit_record.revised`` 等参数。
        处理完成后由 Aldebaran 回调本服务 ``POST /notification/push``（type=visit_record_card）。
        """
        resolved_message_type = message_type or settings.ALDEBARAN_VISIT_RECORD_MESSAGE_TYPE
        resolved_dedupe_key = dedupe_key or f"{resolved_message_type}:{record_id}:v1"
        resolved_payload = payload if payload is not None else {"record_id": record_id}
        resolved_trace_id = trace_id or record_id

        return self.submit_incoming_message(
            message_type=resolved_message_type,
            source_unique_id=record_id,
            source_table="crm_sales_visit_records",
            payload=resolved_payload,
            event_time=event_time or datetime.now(timezone.utc),
            dedupe_key=resolved_dedupe_key,
            trace_id=resolved_trace_id,
            timeout_seconds=timeout_seconds,
        )

    def trigger_local_contact_created(
        self,
        *,
        contact_id: str,
        customer_id: Optional[str] = None,
        created_by_user_id: Optional[UUID] = None,
        event_time: Optional[datetime] = None,
        message_type: Optional[str] = None,
        dedupe_key: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """
        本地联系人创建事件入队 Aldebaran（POST /api/v1/messages/incoming）。
        默认 ``crm.contact.created``；可用 ``message_type`` / ``payload`` 覆盖。
        """
        resolved_message_type = message_type or settings.ALDEBARAN_CONTACT_CREATED_MESSAGE_TYPE
        resolved_dedupe_key = dedupe_key or f"{resolved_message_type}:{contact_id}:v1"
        resolved_trace_id = trace_id or contact_id
        if payload is not None:
            resolved_payload = payload
        else:
            resolved_payload: dict[str, Any] = {"contact_id": contact_id}
            if customer_id:
                resolved_payload["customer_id"] = customer_id
            if created_by_user_id is not None:
                resolved_payload["created_by_user_id"] = str(created_by_user_id)

        return self.submit_incoming_message(
            message_type=resolved_message_type,
            source_unique_id=contact_id,
            source_table="local_contacts",
            payload=resolved_payload,
            event_time=event_time or datetime.now(timezone.utc),
            dedupe_key=resolved_dedupe_key,
            trace_id=resolved_trace_id,
            timeout_seconds=timeout_seconds,
        )


aldebaran_client = AldebaranClient()

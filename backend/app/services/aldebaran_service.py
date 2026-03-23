import logging
from typing import Any, Optional

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
            review_session_recalc_path or getattr(settings, "ALDEBARAN_REVIEW_SESSION_RECALC_PATH", ""),
            default="/api/v1/review/session/recalculate-forecast-aggregates",
        )

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
            "department": department_name,  # None -> null（公司周报）
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

        # 推送侧依赖 department_name 做接收者定位；兜底只依赖调用参数，不依赖接口返回
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
        """
        调用 Aldebaran CVGG 服务生成客户拜访指引。

        返回值为 Aldebaran 返回的 `data` 字段内容（具体结构由服务端约定）。
        """
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
        period: str,
        recalc_scope: str,
        owner_id: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """
        通知 Aldebaran 按 review session 重算 forecast 聚合；结果仅以接口返回为准，调用方不做本地 DB 兜底。

        - recalc_scope: ``full_session``（leader 全量）或 ``self_only``（单人）。
        - self_only 时建议传 owner_id（参会人 crm_user_id）。
        """
        path = self._review_session_recalc_path
        if not path:
            raise RuntimeError("ALDEBARAN_REVIEW_SESSION_RECALC_PATH is not configured")
        url = f"{self._base_url}{_normalize_path(path, default=path)}"
        payload: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "session_id": session_id,
            "period": period,
            "recalc_scope": recalc_scope,
        }
        if owner_id:
            payload["owner_id"] = owner_id
        logger.info(
            "调用 Aldebaran review session 重算: %s, session_id=%s period=%s",
            url,
            session_id,
            period,
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


aldebaran_client = AldebaranClient()

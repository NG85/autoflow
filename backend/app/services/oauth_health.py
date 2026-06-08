"""
Read-only connectivity probe for aptsell-oauth.

Never raises to callers; safe for health endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, TypedDict

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

OAuthProbeStatus = Literal["ok", "degraded", "disabled"]


class OAuthHealthResult(TypedDict):
    status: OAuthProbeStatus
    oauth_base_url: str
    health_url: str
    reachable: bool
    http_status: int | None
    latency_ms: float | None
    error: str | None


def probe_oauth_service() -> OAuthHealthResult:
    base = (settings.OAUTH_BASE_URL or "").rstrip("/")
    health_url = f"{base}/health" if base else ""

    result: OAuthHealthResult = {
        "status": "disabled",
        "oauth_base_url": base,
        "health_url": health_url,
        "reachable": False,
        "http_status": None,
        "latency_ms": None,
        "error": None,
    }

    if not settings.OAUTH_HEALTH_PROBE_ENABLED:
        return result

    if not base:
        result["status"] = "degraded"
        result["error"] = "OAUTH_BASE_URL is empty"
        return result

    timeout = settings.OAUTH_HEALTH_PROBE_TIMEOUT_SECONDS
    started = time.perf_counter()
    try:
        resp = requests.get(health_url, timeout=timeout)
        latency_ms = (time.perf_counter() - started) * 1000
        result["latency_ms"] = round(latency_ms, 2)
        result["http_status"] = resp.status_code
        result["reachable"] = resp.ok
        if resp.ok:
            result["status"] = "ok"
        else:
            result["status"] = "degraded"
            result["error"] = f"unexpected status {resp.status_code}"
    except requests.RequestException as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        result["latency_ms"] = round(latency_ms, 2)
        result["status"] = "degraded"
        result["error"] = str(exc)
        logger.debug("OAuth health probe failed: %s", exc, exc_info=True)

    return result


def oauth_health_response_body() -> dict[str, Any]:
    """JSON body for GET /healthz/oauth (HTTP status always 200)."""
    return probe_oauth_service()

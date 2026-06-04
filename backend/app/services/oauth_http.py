"""
HTTP transport for oauth service clients (timeout, retries, metrics).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, Optional

import requests
from prometheus_client import Counter, Histogram

from app.core.config import settings

logger = logging.getLogger(__name__)

OAuthRequestOutcome = Literal["success", "http_error", "transport_error", "parse_error"]

OAUTH_CLIENT_REQUESTS = Counter(
    "oauth_client_requests_total",
    "Total OAuth service HTTP requests from autoflow",
    ["operation", "outcome"],
)
OAUTH_CLIENT_LATENCY = Histogram(
    "oauth_client_request_duration_seconds",
    "OAuth service HTTP request latency from autoflow",
    ["operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)


def _resolve_timeout(timeout_seconds: Optional[float]) -> float:
    if timeout_seconds is not None:
        return float(timeout_seconds)
    return float(settings.OAUTH_CLIENT_DEFAULT_TIMEOUT_SECONDS)


def _sleep_backoff(attempt: int) -> None:
    delay = float(settings.OAUTH_CLIENT_RETRY_BACKOFF_SECONDS) * (2**attempt)
    if delay > 0:
        time.sleep(delay)


def _request_json(
    session: requests.Session,
    *,
    method: Literal["GET", "POST"],
    base_url: str,
    operation: str,
    path: str,
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout_seconds: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """
    HTTP request to oauth service. Returns parsed JSON dict on HTTP 2xx, else None.

    Retries only on transport failures (connection/timeout), not on HTTP 4xx/5xx.
    """
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    timeout = _resolve_timeout(timeout_seconds)
    merged_headers = dict(headers or {})
    if method == "POST":
        merged_headers.setdefault("Content-Type", "application/json")
    max_attempts = max(1, 1 + int(settings.OAUTH_CLIENT_RETRY_ATTEMPTS))

    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        started = time.perf_counter()
        try:
            if method == "POST":
                resp = session.post(
                    url,
                    json=json_body,
                    headers=merged_headers,
                    timeout=timeout,
                )
            else:
                resp = session.get(url, headers=merged_headers, timeout=timeout)
            elapsed = time.perf_counter() - started
            OAUTH_CLIENT_LATENCY.labels(operation=operation).observe(elapsed)

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                OAUTH_CLIENT_REQUESTS.labels(operation=operation, outcome="http_error").inc()
                logger.warning(
                    "OAuth %s HTTP error: status=%s url=%s elapsed_ms=%.1f",
                    operation,
                    resp.status_code,
                    url,
                    elapsed * 1000,
                    exc_info=exc,
                )
                return None

            try:
                data = resp.json()
            except ValueError as exc:
                OAUTH_CLIENT_REQUESTS.labels(operation=operation, outcome="parse_error").inc()
                logger.warning(
                    "OAuth %s invalid JSON: url=%s elapsed_ms=%.1f",
                    operation,
                    url,
                    elapsed * 1000,
                    exc_info=exc,
                )
                return None

            if not isinstance(data, dict):
                OAUTH_CLIENT_REQUESTS.labels(operation=operation, outcome="parse_error").inc()
                logger.warning(
                    "OAuth %s non-object JSON: type=%s url=%s",
                    operation,
                    type(data).__name__,
                    url,
                )
                return None

            OAUTH_CLIENT_REQUESTS.labels(operation=operation, outcome="success").inc()
            logger.debug(
                "OAuth %s ok: status=%s elapsed_ms=%.1f",
                operation,
                resp.status_code,
                elapsed * 1000,
            )
            return data

        except requests.RequestException as exc:
            last_exc = exc
            elapsed = time.perf_counter() - started
            OAUTH_CLIENT_LATENCY.labels(operation=operation).observe(elapsed)
            if attempt + 1 < max_attempts:
                logger.warning(
                    "OAuth %s transport error (retry %s/%s): url=%s err=%s",
                    operation,
                    attempt + 1,
                    max_attempts,
                    url,
                    exc,
                )
                _sleep_backoff(attempt)
                continue
            OAUTH_CLIENT_REQUESTS.labels(operation=operation, outcome="transport_error").inc()
            logger.exception(
                "OAuth %s transport error: url=%s elapsed_ms=%.1f",
                operation,
                url,
                elapsed * 1000,
            )
            return None

    if last_exc:
        logger.debug("OAuth %s exhausted retries: %s", operation, last_exc)
    return None


def post_json(
    session: requests.Session,
    *,
    base_url: str,
    operation: str,
    path: str,
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout_seconds: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    return _request_json(
        session,
        method="POST",
        base_url=base_url,
        operation=operation,
        path=path,
        json_body=json_body,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )


def get_json(
    session: requests.Session,
    *,
    base_url: str,
    operation: str,
    path: str,
    headers: Optional[dict[str, str]] = None,
    timeout_seconds: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    return _request_json(
        session,
        method="GET",
        base_url=base_url,
        operation=operation,
        path=path,
        headers=headers,
        timeout_seconds=timeout_seconds,
    )

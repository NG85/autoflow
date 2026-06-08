"""
Issue oauth_access_token after successful POST /auth/login or on logout cleanup.

When AUTH_LEGACY_OAUTH_SHADOW_ENABLED=true, login calls oauth session/issue by users.id.
Optional AUTH_LEGACY_OAUTH_SHADOW_PASSWORD_FALLBACK uses session/login with form credentials.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request, Response
from prometheus_client import Counter

from app.auth.oauth_cookies import (
    clear_oauth_access_cookie,
    get_oauth_access_token_from_request,
    set_oauth_access_cookie,
)
from app.core.config import settings
from app.models import User
from app.services.oauth_session_client import (
    OAuthSessionLoginResult,
    oauth_session_client,
)

logger = logging.getLogger(__name__)

AUTH_LEGACY_OAUTH_SHADOW_TOTAL = Counter(
    "auth_legacy_oauth_shadow_total",
    "OAuth shadow on legacy login/logout",
    ["event", "result"],
)


def is_legacy_oauth_shadow_enabled() -> bool:
    return bool(settings.AUTH_LEGACY_OAUTH_SHADOW_ENABLED)


def _session_issue_configured() -> bool:
    return bool(
        settings.OAUTH_SESSION_ISSUE_ENABLED
        and (settings.OAUTH_SESSION_ISSUE_SECRET or "").strip()
    )


def _login_channel() -> str:
    return settings.OAUTH_LOGIN_CHANNEL or settings.OAUTH_REGISTER_CHANNEL


def _apply_session_result(
    response: Response,
    result: OAuthSessionLoginResult,
    *,
    log_label: str,
) -> OAuthSessionLoginResult:
    set_oauth_access_cookie(response, result.access_token, result.expires_in)
    AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="success").inc()
    logger.info(
        "OAuth shadow cookie set via %s for user_id=%s",
        log_label,
        result.user_id,
    )
    return result


def issue_oauth_session_cookie(
    response: Response,
    *,
    login_id: str,
    password: str,
    channel: Optional[str] = None,
) -> Optional[OAuthSessionLoginResult]:
    """
    Call oauth session/login (explicit oauth credentials). Used by login/oauth BFF.
    """
    login_id = (login_id or "").strip()
    password = password or ""
    if not login_id or not password:
        AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="skipped_no_credentials").inc()
        return None

    result = oauth_session_client.login(
        user_id=login_id,
        password=password,
        channel=channel or _login_channel(),
    )
    if not result:
        AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="oauth_failed").inc()
        logger.info("OAuth shadow login failed for login_id=%s (legacy login unaffected)", login_id)
        return None

    return _apply_session_result(response, result, log_label="session/login")


def issue_oauth_session_for_user(
    response: Response,
    user: User,
    *,
    channel: Optional[str] = None,
) -> Optional[OAuthSessionLoginResult]:
    """
    Issue oauth session via trusted /session/issue using autoflow users.id.
    """
    if not _session_issue_configured():
        AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="skipped_issue_disabled").inc()
        logger.debug("OAuth session issue skipped: OAUTH_SESSION_ISSUE_SECRET not configured")
        return None

    result = oauth_session_client.issue_session_for_user(
        user.id,
        channel=channel or _login_channel(),
    )
    if not result:
        AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="issue_failed").inc()
        logger.info(
            "OAuth session issue failed for users.id=%s (legacy login unaffected)",
            user.id,
        )
        return None

    return _apply_session_result(response, result, log_label="session/issue")


async def _read_login_form_credentials(
    request: Optional[Request],
) -> tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    try:
        form = await request.form()
    except Exception:
        logger.debug("OAuth shadow: could not read login form", exc_info=True)
        return None, None
    username = form.get("username")
    password = form.get("password")
    login_id = str(username).strip() if username is not None else None
    pwd = str(password) if password is not None else None
    return login_id or None, pwd


async def attach_oauth_cookie_after_legacy_login(
    user: User,
    request: Optional[Request],
    response: Optional[Response],
) -> None:
    """Legacy login path: issue oauth session after password auth."""
    if not is_legacy_oauth_shadow_enabled():
        return
    if response is None:
        AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="skipped_no_response").inc()
        return

    if issue_oauth_session_for_user(response, user):
        return

    if not settings.AUTH_LEGACY_OAUTH_SHADOW_PASSWORD_FALLBACK:
        return

    login_id, password = await _read_login_form_credentials(request)
    if not login_id:
        login_id = (user.email or str(user.id)).strip()
    if not password:
        AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="login", result="skipped_no_password").inc()
        return

    issue_oauth_session_cookie(response, login_id=login_id, password=password)


def clear_oauth_cookie_on_legacy_logout(
    request: Request,
    response: Response,
) -> None:
    """Best-effort oauth logout when legacy POST /auth/logout completes."""
    if not is_legacy_oauth_shadow_enabled():
        return

    token = get_oauth_access_token_from_request(request)
    if token:
        oauth_session_client.logout(token)

    clear_oauth_access_cookie(response)
    AUTH_LEGACY_OAUTH_SHADOW_TOTAL.labels(event="logout", result="cleared").inc()


def is_legacy_logout_request(request: Request) -> bool:
    return request.method == "POST" and request.url.path.rstrip("/").endswith("/auth/logout")

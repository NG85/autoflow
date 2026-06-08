"""
User resolution for incoming requests (auth final state: oauth-only).
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import Request
from prometheus_client import Counter
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.api_keys import api_key_manager
from app.auth.oauth_cookies import get_oauth_access_token_from_request
from app.models import User
from app.services.oauth_session_client import oauth_session_client

AuthMode = Literal["oauth"]
AuthSource = Literal["api_key", "oauth_bearer", "oauth_cookie", "none"]

AUTH_RESOLVE_TOTAL = Counter(
    "auth_resolve_total",
    "Auth user resolution attempts",
    ["mode", "source"],
)

API_KEY_PREFIX = "ta-"


async def _load_active_user(session: AsyncSession, user_id) -> Optional[User]:
    user = await session.get(User, user_id)
    if not user or not user.is_active or not user.is_verified:
        return None
    return user


async def resolve_oauth_bearer_user(
    session: AsyncSession,
    bearer_token: str,
) -> Optional[User]:
    user_id = oauth_session_client.resolve_user_id_from_bearer(bearer_token)
    if not user_id:
        return None
    return await _load_active_user(session, user_id)


async def _try_resolve_oauth_token(
    session: AsyncSession,
    token: str,
    *,
    mode: AuthMode,
    source: AuthSource,
) -> Optional[tuple[User, AuthSource]]:
    oauth_user = await resolve_oauth_bearer_user(session, token)
    if not oauth_user:
        return None
    AUTH_RESOLVE_TOTAL.labels(mode=mode, source=source).inc()
    return oauth_user, source


async def _resolve_api_key_user(
    request: Request,
    session: AsyncSession,
    *,
    mode: AuthMode,
    bearer: Optional[str] = None,
    bearer_is_api_key: bool = False,
) -> Optional[tuple[User, AuthSource]]:
    # Non ta- Bearer tokens are oauth JWTs, not API keys.
    if bearer is not None and not bearer_is_api_key:
        return None
    api_user = await api_key_manager.get_active_user_from_request(session, request)
    if not api_user:
        return None
    AUTH_RESOLVE_TOTAL.labels(mode=mode, source="api_key").inc()
    return api_user, "api_key"


async def _try_resolve_oauth(
    session: AsyncSession,
    request: Request,
    *,
    mode: AuthMode,
    bearer: Optional[str],
    bearer_is_api_key: bool,
    oauth_cookie: Optional[str],
) -> Optional[tuple[User, AuthSource]]:
    if bearer and not bearer_is_api_key:
        resolved = await _try_resolve_oauth_token(
            session, bearer, mode=mode, source="oauth_bearer"
        )
        if resolved:
            return resolved

    if oauth_cookie:
        resolved = await _try_resolve_oauth_token(
            session, oauth_cookie, mode=mode, source="oauth_cookie"
        )
        if resolved:
            return resolved

    return None


async def _resolve_oauth_strict(
    request: Request,
    session: AsyncSession,
    *,
    mode: AuthMode,
    bearer: Optional[str],
    bearer_is_api_key: bool,
    oauth_cookie: Optional[str],
) -> tuple[Optional[User], AuthSource]:
    api_resolved = await _resolve_api_key_user(
        request,
        session,
        mode=mode,
        bearer=bearer,
        bearer_is_api_key=bearer_is_api_key,
    )
    if api_resolved:
        return api_resolved

    oauth_resolved = await _try_resolve_oauth(
        session,
        request,
        mode=mode,
        bearer=bearer,
        bearer_is_api_key=bearer_is_api_key,
        oauth_cookie=oauth_cookie,
    )
    if oauth_resolved:
        return oauth_resolved

    AUTH_RESOLVE_TOTAL.labels(mode=mode, source="none").inc()
    return None, "none"


async def resolve_user(
    request: Request,
    session: AsyncSession,
) -> tuple[Optional[User], AuthSource]:
    """Resolve the current user without raising HTTPException (oauth-only)."""
    bearer = get_bearer_token(request)
    bearer_is_api_key = bool(bearer and is_api_key_bearer(bearer))
    oauth_cookie = get_oauth_access_token_from_request(request)

    return await _resolve_oauth_strict(
        request,
        session,
        mode="oauth",
        bearer=bearer,
        bearer_is_api_key=bearer_is_api_key,
        oauth_cookie=oauth_cookie,
    )


def get_bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization")
    if not header or not header.startswith("Bearer "):
        return None
    return header[7:].strip()


def is_api_key_bearer(token: str) -> bool:
    return token.startswith(API_KEY_PREFIX)

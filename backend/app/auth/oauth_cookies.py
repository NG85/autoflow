"""HttpOnly cookie helpers for oauth access token."""

from __future__ import annotations

from fastapi import Response

from app.core.config import settings


def set_oauth_access_cookie(response: Response, access_token: str, max_age: int) -> None:
    response.set_cookie(
        key=settings.OAUTH_ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        max_age=max_age,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.OAUTH_ACCESS_TOKEN_COOKIE_SAMESITE,
        path="/",
    )


def clear_oauth_access_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.OAUTH_ACCESS_TOKEN_COOKIE_NAME,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.OAUTH_ACCESS_TOKEN_COOKIE_SAMESITE,
    )


def get_oauth_access_token_from_request(request) -> str | None:
    token = request.cookies.get(settings.OAUTH_ACCESS_TOKEN_COOKIE_NAME)
    if not token or not str(token).strip():
        return None
    return str(token).strip()

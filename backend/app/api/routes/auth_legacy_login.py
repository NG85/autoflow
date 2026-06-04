"""
Form login and logout (POST /auth/login, POST /auth/logout).

Validates credentials against users, then issues oauth_access_token via session/issue.
Does not write user_sessions rows.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from app.auth.error_codes import LoginErrorCode
from app.auth.oauth_cookies import clear_oauth_access_cookie
from app.auth.oauth_shadow import (
    attach_oauth_cookie_after_legacy_login,
    clear_oauth_cookie_on_legacy_logout,
)
from app.auth.user_repository import UserRepository
from app.auth.users import user_repository
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/login", name="auth:legacy.login")
async def login_without_session(
    request: Request,
    credentials: OAuth2PasswordRequestForm = Depends(),
    repo: UserRepository = Depends(user_repository),
) -> Response:
    """
    Validate credentials against users table; issue oauth session cookie on success.
    """
    user = await repo.authenticate(credentials)

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=LoginErrorCode.LOGIN_BAD_CREDENTIALS,
        )
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=LoginErrorCode.LOGIN_USER_NOT_VERIFIED,
        )

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    await attach_oauth_cookie_after_legacy_login(user, request, response)
    logger.debug("Legacy login without session write for user_id=%s", user.id)
    return response


@router.post("/logout", name="auth:legacy.logout")
async def logout_without_session(request: Request) -> Response:
    """
    Logout without requiring a valid user_sessions token (oauth-only users OK).

    Best-effort: clear legacy session cookie if present; clear oauth cookie via shadow helper.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)

    session_cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_cookie:
        response.delete_cookie(
            key=settings.SESSION_COOKIE_NAME,
            path="/",
            secure=settings.SESSION_COOKIE_SECURE,
            samesite="none",
        )

    clear_oauth_access_cookie(response)
    clear_oauth_cookie_on_legacy_logout(request, response)
    return response

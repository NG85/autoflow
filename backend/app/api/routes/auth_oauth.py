"""
OAuth BFF login routes (POST /auth/login/oauth, POST /auth/logout/oauth).

Optional alternative to form POST /auth/login; gated by OAUTH_BFF_LOGIN_ENABLED.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.auth.oauth_cookies import (
    clear_oauth_access_cookie,
    get_oauth_access_token_from_request,
)
from app.auth.oauth_shadow import issue_oauth_session_cookie
from app.core.config import settings
from app.services.oauth_session_client import oauth_session_client

logger = logging.getLogger(__name__)

router = APIRouter()


class OAuthBffLoginRequest(BaseModel):
    """Aligns with oauth session/login; accepts frontend-style `username`."""

    password: str = Field(..., min_length=1)
    channel: str = Field(default="siaweb")
    user_id: Optional[str] = Field(default=None, description="平台登录名 / 邮箱")
    username: Optional[str] = Field(default=None, description="与 user_id 等价，兼容 autoflow 前端")

    @model_validator(mode="after")
    def _require_identity(self) -> "OAuthBffLoginRequest":
        if not (self.user_id or self.username or "").strip():
            raise ValueError("user_id or username is required")
        return self

    @property
    def resolved_user_id(self) -> str:
        return (self.user_id or self.username or "").strip()


class OAuthBffLoginResponse(BaseModel):
    status: str = "success"
    message: str = "Login successful"
    user_id: str
    expires_in: int
    auth_provider: str = "oauth"


def _ensure_bff_enabled() -> None:
    if not settings.OAUTH_BFF_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="OAuth BFF login is disabled")


@router.post("/login/oauth", response_model=OAuthBffLoginResponse)
async def login_oauth(req: OAuthBffLoginRequest, response: Response) -> OAuthBffLoginResponse:
    """
    Proxy to aptsell-oauth POST /oauth/v1/session/login and set HttpOnly cookie.

    Subsequent API calls use oauth cookie resolution by default.
    Legacy session cookie is not created; existing POST /auth/login is unchanged.
    """
    _ensure_bff_enabled()

    result = issue_oauth_session_cookie(
        response,
        login_id=req.resolved_user_id,
        password=req.password,
        channel=req.channel,
    )
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    logger.info("OAuth BFF login succeeded for user_id=%s", result.user_id)

    return OAuthBffLoginResponse(
        user_id=result.user_id,
        expires_in=result.expires_in,
    )


@router.post("/logout/oauth")
async def logout_oauth(request: Request, response: Response) -> dict:
    """Clear oauth cookie and best-effort call oauth session/logout."""
    _ensure_bff_enabled()

    token = get_oauth_access_token_from_request(request)
    if token:
        oauth_session_client.logout(token)

    clear_oauth_access_cookie(response)
    return {"status": "success", "message": "Logged out"}

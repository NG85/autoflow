"""
OAuth session API client (login, issue, logout, me).

Resolves Bearer JWT via GET /oauth/v1/session/me.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

import requests

from app.core.config import settings
from app.services.oauth_http import get_json, post_json

logger = logging.getLogger(__name__)


class OAuthSessionLoginResult:
    __slots__ = ("access_token", "expires_in", "user_id")

    def __init__(self, *, access_token: str, expires_in: int, user_id: str):
        self.access_token = access_token
        self.expires_in = expires_in
        self.user_id = user_id


class OAuthSessionClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._base_url = (base_url or settings.OAUTH_BASE_URL).rstrip("/")
        self._session = session or requests.Session()

    def login(
        self,
        *,
        user_id: str,
        password: str,
        channel: str = "siaweb",
        timeout_seconds: Optional[float] = None,
    ) -> Optional[OAuthSessionLoginResult]:
        """
        POST /oauth/v1/session/login — returns token metadata on success.
        """
        data = post_json(
            self._session,
            base_url=self._base_url,
            operation="session_login",
            path="/oauth/v1/session/login",
            json_body={
                "user_id": user_id,
                "password": password,
                "channel": channel,
            },
            timeout_seconds=timeout_seconds,
        )
        return self._parse_login_result(data)

    @staticmethod
    def _parse_login_result(data: Optional[dict]) -> Optional[OAuthSessionLoginResult]:
        if not data or data.get("code") != 0:
            return None
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        access_token = result.get("accessToken") or result.get("access_token")
        expires_in = result.get("expiresIn") or result.get("expires_in") or 0
        user = result.get("user") if isinstance(result.get("user"), dict) else result
        raw_uid = None
        if isinstance(user, dict):
            raw_uid = user.get("userId") or user.get("user_id")
        if not access_token or not raw_uid:
            return None
        return OAuthSessionLoginResult(
            access_token=str(access_token),
            expires_in=int(expires_in),
            user_id=str(raw_uid),
        )

    def issue_session_for_user(
        self,
        system_user_id: UUID,
        *,
        channel: str = "siaweb",
        timeout_seconds: Optional[float] = None,
    ) -> Optional[OAuthSessionLoginResult]:
        """
        POST /oauth/v1/session/issue — trusted issue by users.id (no oauth password).

        Requires OAUTH_SESSION_ISSUE_SECRET shared with aptsell-oauth.
        """
        secret = (settings.OAUTH_SESSION_ISSUE_SECRET or "").strip()
        if not secret:
            return None

        data = post_json(
            self._session,
            base_url=self._base_url,
            operation="session_issue",
            path="/oauth/v1/session/issue",
            json_body={
                "user_id": str(system_user_id),
                "channel": channel,
            },
            headers={"Authorization": f"Bearer {secret}"},
            timeout_seconds=timeout_seconds,
        )
        if not data or data.get("code") != 0:
            logger.debug("OAuth session/issue failed: %s", data)
            return None
        return self._parse_login_result(data)

    def logout(self, bearer_token: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """POST /oauth/v1/session/logout."""
        token = (bearer_token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            return False

        timeout = timeout_seconds
        if timeout is None:
            timeout = settings.OAUTH_SESSION_ME_TIMEOUT_SECONDS

        data = post_json(
            self._session,
            base_url=self._base_url,
            operation="session_logout",
            path="/oauth/v1/session/logout",
            headers={"Authorization": f"Bearer {token}"},
            timeout_seconds=timeout,
        )
        return bool(data and data.get("code") == 0)

    def resolve_user_id_from_bearer(
        self,
        bearer_token: str,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[UUID]:
        """
        Validate JWT session via oauth /session/me.

        Returns users.id (UUID) when code==0 and result contains userId/user_id.
        """
        token = (bearer_token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            return None

        timeout = timeout_seconds
        if timeout is None:
            timeout = settings.OAUTH_SESSION_ME_TIMEOUT_SECONDS

        data = get_json(
            self._session,
            base_url=self._base_url,
            operation="session_me",
            path="/oauth/v1/session/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout_seconds=timeout,
        )
        if not data or data.get("code") != 0:
            logger.debug("OAuth session/me failed or non-zero code: %s", data)
            return None

        result = data.get("result")
        if not isinstance(result, dict):
            return None

        raw_id = result.get("userId") or result.get("user_id")
        if not raw_id:
            return None

        try:
            return UUID(str(raw_id))
        except (ValueError, TypeError):
            logger.warning("OAuth session/me returned invalid user id: %s", raw_id)
            return None


oauth_session_client = OAuthSessionClient()

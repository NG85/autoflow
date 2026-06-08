"""
OAuth user registration client (POST /oauth/v1/user/register).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import requests

from app.core.config import settings
from app.services.oauth_http import post_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OAuthRegisterResult:
    user_id: str
    email: Optional[str] = None
    already_existed: bool = False


class OAuthRegistrationClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._base_url = (base_url or settings.OAUTH_BASE_URL).rstrip("/")
        self._session = session or requests.Session()

    def register_user(
        self,
        *,
        user_id: str,
        password: str,
        name: str,
        email: Optional[str] = None,
        channel: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[OAuthRegisterResult]:
        """
        Register via oauth service. Returns None on hard failure.

        ``already_existed`` is set when oauth reports duplicate account.
        """
        channel = channel or settings.OAUTH_REGISTER_CHANNEL
        payload: dict[str, Any] = {
            "user_id": user_id,
            "password": password,
            "name": name,
            "channel": channel,
        }
        if email:
            payload["email"] = email

        url = f"{self._base_url}/oauth/v1/user/register"
        timeout = timeout_seconds or settings.OAUTH_CLIENT_DEFAULT_TIMEOUT_SECONDS

        try:
            resp = self._session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            data = resp.json() if resp.content else {}
        except (requests.RequestException, ValueError) as exc:
            logger.exception("OAuth user/register request failed: %s", exc)
            return None

        if not isinstance(data, dict):
            return None

        if resp.ok and data.get("code") == 0:
            return self._parse_success(data, email=email, already_existed=False)

        message = str(data.get("message") or "")
        if self._is_duplicate_account(resp.status_code, data, message):
            logger.info("OAuth user/register: account already exists for user_id=%s", user_id)
            return OAuthRegisterResult(
                user_id=user_id,
                email=email,
                already_existed=True,
            )

        logger.warning(
            "OAuth user/register failed: status=%s code=%s message=%s",
            resp.status_code,
            data.get("code"),
            message,
        )
        return None

    def _parse_success(
        self,
        data: dict[str, Any],
        *,
        email: Optional[str],
        already_existed: bool,
    ) -> Optional[OAuthRegisterResult]:
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        raw_uid = result.get("userId") or result.get("user_id")
        if not raw_uid:
            return None
        resolved_email = email or result.get("email") or result.get("askUserId")
        return OAuthRegisterResult(
            user_id=str(raw_uid),
            email=str(resolved_email) if resolved_email else email,
            already_existed=already_existed,
        )

    @staticmethod
    def _is_duplicate_account(status_code: int, data: dict[str, Any], message: str) -> bool:
        if status_code not in (400, 409):
            return False
        if data.get("code") in (1004, 1006):
            return True
        lowered = message.lower()
        return "已存在" in message or "already exists" in lowered or "registered" in lowered


oauth_registration_client = OAuthRegistrationClient()

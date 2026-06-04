"""OAuth cookie on form login/logout."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import Response

from app.auth.oauth_shadow import (
    attach_oauth_cookie_after_legacy_login,
    clear_oauth_cookie_on_legacy_logout,
    is_legacy_logout_request,
    issue_oauth_session_cookie,
    issue_oauth_session_for_user,
)
from app.core.config import settings
from app.models import User
from app.services.oauth_session_client import OAuthSessionLoginResult

USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _user() -> User:
    return User(
        id=USER_ID,
        email="test@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )


class TestIssueOauthSessionCookie:
    def test_skipped_without_credentials(self):
        response = Response()
        with patch(
            "app.auth.oauth_shadow.oauth_session_client.login",
        ) as login:
            assert issue_oauth_session_cookie(response, login_id="", password="x") is None
            assert issue_oauth_session_cookie(response, login_id="u", password="") is None
        login.assert_not_called()

    def test_sets_cookie_on_success(self):
        response = Response()
        login_result = OAuthSessionLoginResult(
            access_token="jwt.shadow",
            expires_in=7200,
            user_id=str(USER_ID),
        )
        with patch(
            "app.auth.oauth_shadow.oauth_session_client.login",
            return_value=login_result,
        ):
            result = issue_oauth_session_cookie(
                response,
                login_id="test@example.com",
                password="secret",
            )
        assert result is login_result
        assert response.headers.get("set-cookie", "").find("oauth_access_token") >= 0


class TestIssueOauthSessionForUser:
    def test_issue_by_users_id(self, monkeypatch):
        monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_ENABLED", True)
        monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_SECRET", "shared-secret")
        response = Response()
        login_result = OAuthSessionLoginResult(
            access_token="jwt.issue",
            expires_in=3600,
            user_id=str(USER_ID),
        )
        with patch(
            "app.auth.oauth_shadow.oauth_session_client.issue_session_for_user",
            return_value=login_result,
        ) as issue:
            result = issue_oauth_session_for_user(response, _user())
        issue.assert_called_once_with(USER_ID, channel="siaweb")
        assert result is login_result
        assert "oauth_access_token" in response.headers.get("set-cookie", "")

    def test_skipped_when_issue_secret_missing(self, monkeypatch):
        monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_SECRET", "")
        with patch(
            "app.auth.oauth_shadow.oauth_session_client.issue_session_for_user",
        ) as issue:
            assert issue_oauth_session_for_user(Response(), _user()) is None
        issue.assert_not_called()


class TestAttachAfterLegacyLogin:
    @pytest.mark.asyncio
    async def test_attach_uses_session_issue(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", True)
        monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_SECRET", "shared-secret")
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_PASSWORD_FALLBACK", False)
        response = Response()
        request = MagicMock()

        with patch(
            "app.auth.oauth_shadow.issue_oauth_session_for_user",
            return_value=OAuthSessionLoginResult(
                access_token="t",
                expires_in=60,
                user_id=str(USER_ID),
            ),
        ) as issue:
            await attach_oauth_cookie_after_legacy_login(_user(), request, response)

        issue.assert_called_once()
        assert issue.call_args[0][1].id == USER_ID

    @pytest.mark.asyncio
    async def test_attach_password_fallback_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", True)
        monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_SECRET", "")
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_PASSWORD_FALLBACK", True)
        response = Response()
        request = MagicMock()
        request.form = AsyncMock(
            return_value={"username": "u@example.com", "password": "secret"}
        )

        with patch(
            "app.auth.oauth_shadow.issue_oauth_session_for_user",
            return_value=None,
        ):
            with patch(
                "app.auth.oauth_shadow.issue_oauth_session_cookie",
                return_value=OAuthSessionLoginResult(
                    access_token="t",
                    expires_in=60,
                    user_id=str(USER_ID),
                ),
            ) as pwd_issue:
                await attach_oauth_cookie_after_legacy_login(_user(), request, response)

        pwd_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_skipped_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", False)
        with patch(
            "app.auth.oauth_shadow.issue_oauth_session_for_user",
        ) as issue:
            await attach_oauth_cookie_after_legacy_login(_user(), None, Response())
        issue.assert_not_called()


class TestLegacyLogoutShadow:
    def test_is_legacy_logout_path(self):
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/auth/logout"
        assert is_legacy_logout_request(request) is True

    def test_clear_oauth_on_logout(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", True)
        request = MagicMock()
        request.cookies = {"oauth_access_token": "jwt.shadow"}
        response = Response()
        with patch(
            "app.auth.oauth_shadow.oauth_session_client.logout",
            return_value=True,
        ) as logout:
            clear_oauth_cookie_on_legacy_logout(request, response)
        logout.assert_called_once_with("jwt.shadow")

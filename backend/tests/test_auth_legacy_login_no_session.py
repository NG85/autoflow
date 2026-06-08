"""Form login without user_sessions writes."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.routes import auth_legacy_login
from app.auth.user_repository import UserRepository
from app.auth.users import user_repository
from app.core.config import settings
from app.models import User
from app.services.oauth_session_client import OAuthSessionLoginResult

USER_ID = UUID("00000000-0000-0000-0000-000000000099")


def _user() -> User:
    return User(
        id=USER_ID,
        email="u@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )


@pytest.fixture
def legacy_login_client(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", True)
    monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_ENABLED", True)
    monkeypatch.setattr(settings, "OAUTH_SESSION_ISSUE_SECRET", "test-secret")

    app = FastAPI()
    app.include_router(auth_legacy_login.router, prefix="/auth")

    user = _user()
    repo = AsyncMock(spec=UserRepository)
    repo.authenticate = AsyncMock(return_value=user)

    app.dependency_overrides[user_repository] = lambda: repo

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestLoginWithoutSessionWrite:
    def test_login_sets_oauth_cookie_not_session(self, legacy_login_client):
        issue_result = OAuthSessionLoginResult(
            access_token="jwt.no.session",
            expires_in=3600,
            user_id=str(USER_ID),
        )
        with patch(
            "app.auth.oauth_shadow.oauth_session_client.issue_session_for_user",
            return_value=issue_result,
        ):
            response = legacy_login_client.post(
                "/auth/login",
                data={"username": "u@example.com", "password": "secret"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert "oauth_access_token" in response.cookies
        assert response.cookies.get(settings.SESSION_COOKIE_NAME) in (None, "")

    def test_logout_without_session_token_ok(self, legacy_login_client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", True)
        legacy_login_client.cookies.set("oauth_access_token", "jwt.token")
        with patch("app.auth.oauth_shadow.oauth_session_client.logout"):
            response = legacy_login_client.post("/auth/logout")
        assert response.status_code == status.HTTP_204_NO_CONTENT

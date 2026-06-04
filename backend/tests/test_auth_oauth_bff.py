"""OAuth BFF login routes."""

from unittest.mock import patch

import pytest
from fastapi import status

from app.services.oauth_session_client import OAuthSessionLoginResult


@pytest.fixture(autouse=True)
def _enable_bff(monkeypatch):
    monkeypatch.setattr("app.api.routes.auth_oauth.settings.OAUTH_BFF_LOGIN_ENABLED", True)


class TestOAuthBffLogin:
    def test_login_oauth_sets_cookie(self, client):
        login_result = OAuthSessionLoginResult(
            access_token="jwt.token.here",
            expires_in=3600,
            user_id="00000000-0000-0000-0000-000000000001",
        )
        with patch(
            "app.api.routes.auth_oauth.oauth_session_client.login",
            return_value=login_result,
        ):
            response = client.post(
                "/api/v1/auth/login/oauth",
                json={
                    "username": "test@example.com",
                    "password": "secret",
                    "channel": "siaweb",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert data["user_id"] == str(login_result.user_id)
        assert "oauth_access_token" in response.cookies
        assert response.cookies["oauth_access_token"] == "jwt.token.here"

    def test_login_oauth_invalid_credentials(self, client):
        with patch(
            "app.api.routes.auth_oauth.oauth_session_client.login",
            return_value=None,
        ):
            response = client.post(
                "/api/v1/auth/login/oauth",
                json={"username": "u", "password": "bad"},
            )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_oauth_disabled(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes.auth_oauth.settings.OAUTH_BFF_LOGIN_ENABLED", False
        )
        response = client.post(
            "/api/v1/auth/login/oauth",
            json={"username": "u", "password": "p"},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_logout_oauth_clears_cookie(self, client):
        client.cookies.set("oauth_access_token", "jwt.token.here")
        with patch(
            "app.api.routes.auth_oauth.oauth_session_client.logout",
            return_value=True,
        ):
            response = client.post("/api/v1/auth/logout/oauth")
        assert response.status_code == status.HTTP_200_OK
        assert response.cookies.get("oauth_access_token") in ("", None) or (
            "oauth_access_token" not in response.cookies
            or not response.cookies["oauth_access_token"]
        )

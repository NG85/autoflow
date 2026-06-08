"""Auth resolve_user (oauth-only final state)."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import Request

from app.auth.resolve import (
    is_api_key_bearer,
    resolve_user,
)
from app.models import User

USER_ID = UUID("00000000-0000-0000-0000-000000000099")


def _request(headers: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def _user() -> User:
    return User(
        id=USER_ID,
        email="u@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )


@pytest.mark.asyncio
async def test_oauth_bearer_success():
    session = AsyncMock()
    session.get = AsyncMock(return_value=_user())
    jwt_user = _user()

    with patch(
        "app.auth.resolve.resolve_oauth_bearer_user",
        new_callable=AsyncMock,
        return_value=jwt_user,
    ) as oauth_user:
        user, source = await resolve_user(
            _request({"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.x.y"}),
            session,
        )

    assert user == jwt_user
    assert source == "oauth_bearer"
    oauth_user.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_key_skips_oauth_jwt():
    api_user = _user()
    session = AsyncMock()

    with patch(
        "app.auth.resolve.resolve_oauth_bearer_user",
        new_callable=AsyncMock,
    ) as oauth_user:
        with patch(
            "app.auth.resolve.api_key_manager.get_active_user_from_request",
            new_callable=AsyncMock,
            return_value=api_user,
        ):
            user, source = await resolve_user(
                _request({"Authorization": "Bearer ta-secretkey"}),
                session,
            )

    assert user == api_user
    assert source == "api_key"
    oauth_user.assert_not_called()



def test_is_api_key_bearer():
    assert is_api_key_bearer("ta-abc123")
    assert not is_api_key_bearer("eyJhbGciOiJIUzI1NiJ9.payload.sig")


@pytest.mark.asyncio
async def test_oauth_cookie_success():
    jwt_user = _user()
    session = AsyncMock()

    with patch(
        "app.auth.resolve.get_oauth_access_token_from_request",
        return_value="jwt.from.cookie",
    ):
        with patch(
            "app.auth.resolve.resolve_oauth_bearer_user",
            new_callable=AsyncMock,
            return_value=jwt_user,
        ) as oauth_user:
            user, source = await resolve_user(_request(), session)

    assert user == jwt_user
    assert source == "oauth_cookie"
    oauth_user.assert_awaited_once_with(session, "jwt.from.cookie")


@pytest.mark.asyncio
async def test_ignores_session_cookie_without_oauth_token():
    session = AsyncMock()

    with patch(
        "app.auth.resolve.get_oauth_access_token_from_request",
        return_value=None,
    ):
        with patch(
            "app.auth.resolve.api_key_manager.get_active_user_from_request",
            new_callable=AsyncMock,
            return_value=None,
        ):
            user, source = await resolve_user(_request(), session)

    assert user is None
    assert source == "none"


@pytest.mark.asyncio
async def test_api_key_before_oauth_cookie():
    api_user = _user()
    session = AsyncMock()

    with patch(
        "app.auth.resolve.get_oauth_access_token_from_request",
        return_value="jwt.should.not.be.used",
    ):
        with patch(
            "app.auth.resolve.api_key_manager.get_active_user_from_request",
            new_callable=AsyncMock,
            return_value=api_user,
        ) as api_lookup:
            with patch(
                "app.auth.resolve.resolve_oauth_bearer_user",
                new_callable=AsyncMock,
            ) as oauth_resolve:
                user, source = await resolve_user(
                    _request({"Authorization": "Bearer ta-key"}),
                    session,
                )

    assert user == api_user
    assert source == "api_key"
    api_lookup.assert_awaited_once()
    oauth_resolve.assert_not_called()



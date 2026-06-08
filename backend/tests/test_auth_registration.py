"""User registration via oauth."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from app.auth.exceptions import UserAlreadyExists

from app.auth.registration import register_user_account
from app.auth.schemas import UserCreate
from app.core.config import settings
from app.models import User
from app.services.oauth_registration_client import OAuthRegisterResult

USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _user_model() -> User:
    return User(
        id=USER_ID,
        email="test@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )


@pytest.mark.asyncio
async def test_register_legacy_when_oauth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REGISTER_ENABLED", False)
    session = AsyncMock()
    created = _user_model()

    with patch(
        "app.auth.registration.create_user",
        new_callable=AsyncMock,
        return_value=created,
    ) as create:
        result = await register_user_account(
            session,
            UserCreate(email="test@example.com", password="secret"),
        )

    assert result.via_oauth is False
    assert result.user_id == str(USER_ID)
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_via_oauth_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REGISTER_ENABLED", True)
    session = AsyncMock()
    session.get = AsyncMock(return_value=_user_model())
    session.exec = AsyncMock()

    oauth_result = OAuthRegisterResult(
        user_id=str(USER_ID),
        email="test@example.com",
        already_existed=False,
    )

    with patch(
        "app.auth.registration.oauth_registration_client.register_user",
        return_value=oauth_result,
    ) as oauth_register:
        with patch(
            "app.auth.registration.create_user",
            new_callable=AsyncMock,
        ) as create:
            result = await register_user_account(
                session,
                UserCreate(email="test@example.com", password="secret"),
            )

    assert result.via_oauth is True
    assert result.user_id == str(USER_ID)
    oauth_register.assert_called_once()
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_oauth_duplicate_finds_local_user(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REGISTER_ENABLED", True)
    session = AsyncMock()
    existing = _user_model()

    async def _exec(stmt):
        class _Result:
            def first(self_inner):
                return existing

        return _Result()

    session.exec = _exec

    with patch(
        "app.auth.registration.oauth_registration_client.register_user",
        return_value=OAuthRegisterResult(
            user_id="test@example.com",
            email="test@example.com",
            already_existed=True,
        ),
    ):
        result = await register_user_account(
            session,
            UserCreate(email="test@example.com", password="secret"),
        )

    assert result.via_oauth is True
    assert result.already_existed is True
    assert result.user_id == str(USER_ID)


@pytest.mark.asyncio
async def test_register_oauth_failure_falls_back_to_legacy(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REGISTER_ENABLED", True)
    session = AsyncMock()
    created = _user_model()

    with patch(
        "app.auth.registration.oauth_registration_client.register_user",
        return_value=None,
    ):
        with patch(
            "app.auth.registration.create_user",
            new_callable=AsyncMock,
            return_value=created,
        ) as create:
            result = await register_user_account(
                session,
                UserCreate(email="test@example.com", password="secret"),
            )

    assert result.via_oauth is False
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_legacy_user_already_exists(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_REGISTER_ENABLED", False)
    session = AsyncMock()
    existing = _user_model()

    async def _exec(stmt):
        class _Result:
            def first(self_inner):
                return existing

        return _Result()

    session.exec = _exec

    with patch(
        "app.auth.registration.create_user",
        new_callable=AsyncMock,
        side_effect=UserAlreadyExists(),
    ):
        result = await register_user_account(
            session,
            UserCreate(email="test@example.com", password="secret"),
        )

    assert result.already_existed is True
    assert result.user_id == str(USER_ID)


@pytest.mark.asyncio
async def test_ensure_admin_oauth_bootstrap_promotes_existing_user(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_BOOTSTRAP_VIA_OAUTH", True)
    session = AsyncMock()
    session.commit = AsyncMock()
    existing = _user_model()

    oauth_result = OAuthRegisterResult(
        user_id=str(USER_ID),
        email="admin@example.com",
    )

    with patch(
        "app.auth.registration.oauth_registration_client.register_user",
        return_value=oauth_result,
    ):
        with patch(
            "app.auth.registration._find_local_user_after_external_write",
            new_callable=AsyncMock,
            return_value=existing,
        ) as find_user:
            with patch(
                "app.auth.registration._promote_to_superuser",
                new_callable=AsyncMock,
                return_value=existing,
            ) as promote:
                with patch(
                    "app.auth.registration.create_user",
                    new_callable=AsyncMock,
                ) as create_user:
                    from app.auth.registration import ensure_admin_user_account

                    admin = await ensure_admin_user_account(
                        session,
                        email="admin@example.com",
                        password="secret",
                    )

    assert admin == existing
    find_user.assert_awaited_once()
    promote.assert_awaited_once()
    create_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_admin_oauth_bootstrap_does_not_create_duplicate(monkeypatch):
    monkeypatch.setattr(settings, "OAUTH_BOOTSTRAP_VIA_OAUTH", True)
    session = AsyncMock()
    oauth_result = OAuthRegisterResult(
        user_id=str(USER_ID),
        email="admin@example.com",
    )

    with patch(
        "app.auth.registration.oauth_registration_client.register_user",
        return_value=oauth_result,
    ):
        with patch(
            "app.auth.registration._find_local_user_after_external_write",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.auth.registration.create_user",
                new_callable=AsyncMock,
            ) as create_user:
                from app.auth.registration import ensure_admin_user_account

                with pytest.raises(RuntimeError, match="local users row is missing"):
                    await ensure_admin_user_account(
                        session,
                        email="admin@example.com",
                        password="secret",
                    )

    create_user.assert_not_awaited()

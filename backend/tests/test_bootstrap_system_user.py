from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.auth.api_keys import ApiKeyManager
from app.models import User


USER_ID = UUID("00000000-0000-0000-0000-000000000099")
SYSTEM_EMAIL = "sia@aptsell.ai"


def _user(*, email=SYSTEM_EMAIL, is_superuser=False) -> User:
    return User(
        id=USER_ID,
        email=email,
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=is_superuser,
    )


def _session_with_user(user: User | None) -> AsyncMock:
    session = AsyncMock()

    async def _exec(_stmt):
        result = MagicMock()
        result.first.return_value = user
        return result

    session.exec = _exec
    return session


def _api_key(*, display="ta-abcde....xyz"):
    key = MagicMock()
    key.api_key_display = display
    return key


@pytest.mark.asyncio
async def test_ensure_system_user_reuses_account_and_issues_key(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_USER_EMAIL", SYSTEM_EMAIL)
    existing = _user()
    session = _session_with_user(existing)
    raw = "ta-new-secret"

    with patch(
        "app.auth.registration.ensure_system_user_account",
        new_callable=AsyncMock,
    ) as create_account:
        with patch(
            "app.auth.registration.register_system_user_via_oauth",
            return_value=None,
        ) as oauth_register:
            with patch(
                "app.auth.api_keys.api_key_manager.ensure_api_key_for_user",
                new_callable=AsyncMock,
                return_value=(_api_key(), raw),
            ) as ensure_key:
                from bootstrap import ensure_system_user

                await ensure_system_user(session)

    create_account.assert_not_awaited()
    oauth_register.assert_called_once()
    assert oauth_register.call_args.kwargs["email"] == SYSTEM_EMAIL
    ensure_key.assert_awaited_once()
    assert ensure_key.await_args.args[1] is existing
    assert ensure_key.await_args.kwargs["reset"] is False
    out = capsys.readouterr().out
    assert "already exists" in out
    assert raw in out


@pytest.mark.asyncio
async def test_ensure_system_user_skips_superuser_collision(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_USER_EMAIL", SYSTEM_EMAIL)
    session = _session_with_user(_user(is_superuser=True))

    with patch(
        "app.auth.registration.ensure_system_user_account",
        new_callable=AsyncMock,
    ) as create_account:
        with patch(
            "app.auth.registration.register_system_user_via_oauth",
        ) as oauth_register:
            with patch(
                "app.auth.api_keys.api_key_manager.ensure_api_key_for_user",
                new_callable=AsyncMock,
            ) as ensure_key:
                from bootstrap import ensure_system_user

                await ensure_system_user(session)

    create_account.assert_not_awaited()
    oauth_register.assert_not_called()
    ensure_key.assert_not_awaited()
    assert "already belongs to a superuser" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ensure_system_user_creates_account_and_key(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_USER_EMAIL", SYSTEM_EMAIL)
    session = _session_with_user(None)
    created = _user()
    raw = "ta-created-secret"

    with patch(
        "app.auth.registration.ensure_system_user_account",
        new_callable=AsyncMock,
        return_value=created,
    ) as create_account:
        with patch(
            "app.auth.api_keys.api_key_manager.ensure_api_key_for_user",
            new_callable=AsyncMock,
            return_value=(_api_key(), raw),
        ) as ensure_key:
            from bootstrap import ensure_system_user

            await ensure_system_user(session)

    create_account.assert_awaited_once()
    assert create_account.await_args.kwargs["email"] == SYSTEM_EMAIL
    ensure_key.assert_awaited_once()
    out = capsys.readouterr().out
    assert "System user created" in out
    assert raw in out


@pytest.mark.asyncio
async def test_ensure_system_user_skips_existing_key(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_USER_EMAIL", SYSTEM_EMAIL)
    session = _session_with_user(_user())
    display = "ta-exist....key"

    with patch(
        "app.auth.api_keys.api_key_manager.ensure_api_key_for_user",
        new_callable=AsyncMock,
        return_value=(_api_key(display=display), None),
    ):
        with patch(
            "app.auth.registration.register_system_user_via_oauth",
            return_value=None,
        ):
            from bootstrap import ensure_system_user

            await ensure_system_user(session)

    out = capsys.readouterr().out
    assert f"System API key already exists ({display})" in out


@pytest.mark.asyncio
async def test_ensure_system_user_rotates_key(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_USER_EMAIL", SYSTEM_EMAIL)
    session = _session_with_user(_user())
    raw = "ta-rotated-secret"

    with patch(
        "app.auth.api_keys.api_key_manager.ensure_api_key_for_user",
        new_callable=AsyncMock,
        return_value=(_api_key(), raw),
    ) as ensure_key:
        with patch(
            "app.auth.registration.register_system_user_via_oauth",
            return_value=None,
        ):
            from bootstrap import ensure_system_user

            await ensure_system_user(session, reset_api_key=True)

    assert ensure_key.await_args.kwargs["reset"] is True
    out = capsys.readouterr().out
    assert "rotated" in out
    assert raw in out


@pytest.mark.asyncio
async def test_ensure_api_key_reuses_existing():
    existing = MagicMock()
    manager = ApiKeyManager()
    user = _user()

    with patch.object(
        manager, "get_latest_active_api_key", new=AsyncMock(return_value=existing)
    ):
        with patch.object(manager, "acreate_api_key", new=AsyncMock()) as create:
            key, raw = await manager.ensure_api_key_for_user(
                AsyncMock(), user, description="bootstrap system"
            )

    assert key is existing
    assert raw is None
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_api_key_creates_when_missing():
    manager = ApiKeyManager()
    created = MagicMock()
    raw = "ta-raw"

    with patch.object(
        manager, "get_latest_active_api_key", new=AsyncMock(return_value=None)
    ):
        with patch.object(
            manager, "acreate_api_key", new=AsyncMock(return_value=(created, raw))
        ) as create:
            key, secret = await manager.ensure_api_key_for_user(
                AsyncMock(), _user(), description="bootstrap system"
            )

    assert key is created
    assert secret == raw
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_api_key_reset_deactivates_then_creates():
    manager = ApiKeyManager()
    created = MagicMock()
    raw = "ta-new"

    with patch.object(
        manager, "deactivate_active_api_keys", new=AsyncMock()
    ) as deactivate:
        with patch.object(
            manager, "get_latest_active_api_key", new=AsyncMock()
        ) as get_latest:
            with patch.object(
                manager, "acreate_api_key", new=AsyncMock(return_value=(created, raw))
            ):
                key, secret = await manager.ensure_api_key_for_user(
                    AsyncMock(), _user(), description="bootstrap system", reset=True
                )

    deactivate.assert_awaited_once()
    get_latest.assert_not_awaited()
    assert key is created
    assert secret == raw

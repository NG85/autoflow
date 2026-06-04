"""UserRepository password auth."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi.security import OAuth2PasswordRequestForm

from app.auth.exceptions import UserAlreadyExists
from app.auth.schemas import UserCreate
from app.auth.user_repository import UserRepository
from app.models import User

USER_ID = UUID("00000000-0000-0000-0000-000000000042")


def _user(email: str = "u@example.com", hashed: str = "$argon2") -> User:
    return User(
        id=USER_ID,
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )


@pytest.mark.asyncio
async def test_create_raises_when_email_exists():
    session = AsyncMock()
    repo = UserRepository(session)
    session.exec = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=_user())))

    with pytest.raises(UserAlreadyExists):
        await repo.create(UserCreate(email="u@example.com", password="secret"))


@pytest.mark.asyncio
async def test_authenticate_returns_none_for_unknown_email():
    session = AsyncMock()
    repo = UserRepository(session)
    session.exec = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))

    with patch("app.auth.user_repository.hash_for_timing_attack_mitigation") as mit:
        creds = OAuth2PasswordRequestForm(username="nobody@example.com", password="x")
        result = await repo.authenticate(creds)

    assert result is None
    mit.assert_called_once_with("x")


@pytest.mark.asyncio
async def test_authenticate_returns_user_when_password_valid():
    session = AsyncMock()
    repo = UserRepository(session)
    user = _user()
    session.exec = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=user)))

    with patch(
        "app.auth.user_repository.verify_and_update_password",
        return_value=(True, None),
    ):
        creds = OAuth2PasswordRequestForm(username="u@example.com", password="secret")
        result = await repo.authenticate(creds)

    assert result is user
    session.commit.assert_not_awaited()

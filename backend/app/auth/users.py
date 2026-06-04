import logging
import uuid
from http import HTTPStatus
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.exceptions import UserAlreadyExists, UserNotExists
from app.auth.resolve import resolve_user
from app.auth.schemas import UserCreate, UserUpdate
from app.auth.user_repository import UserRepository
from app.core.db import get_db_async_session
from app.models import User

logger = logging.getLogger(__name__)


def user_repository(session: AsyncSession = Depends(get_db_async_session)) -> UserRepository:
    return UserRepository(session)


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_async_session),
) -> User:
    resolved, _source = await resolve_user(request, session)
    if not resolved:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED)
    return resolved


async def current_superuser(
    request: Request,
    session: AsyncSession = Depends(get_db_async_session),
) -> User:
    resolved, _source = await resolve_user(request, session)
    if not resolved:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED)
    if not resolved.is_superuser:
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN)
    return resolved


async def optional_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_async_session),
) -> Optional[User]:
    resolved, _source = await resolve_user(request, session)
    return resolved


async def resolve_user_for_me(
    request: Request,
    session: AsyncSession,
) -> User:
    """Resolve user for GET /users/me."""
    user, _source = await resolve_user(request, session)
    if not user:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED)
    return user


async def create_user(
    session: AsyncSession,
    email: str,
    password: str,
    is_active: bool = True,
    is_verified: bool = True,
    is_superuser: bool = False,
) -> User:
    repo = UserRepository(session)
    try:
        return await repo.create(
            UserCreate(
                email=email,
                password=password,
                is_active=is_active,
                is_verified=is_verified,
                is_superuser=is_superuser,
            )
        )
    except UserAlreadyExists:
        logger.error("User %s already exists", email)
        raise


async def update_user_password(
    session: AsyncSession,
    user_id: uuid.UUID,
    new_password: str,
) -> User:
    repo = UserRepository(session)
    try:
        return await repo.update_password(user_id, new_password)
    except UserNotExists as e:
        logger.error(str(e))
        raise
    except Exception as e:
        logger.error("Failed to update password for user %s: %s", user_id, e)
        raise

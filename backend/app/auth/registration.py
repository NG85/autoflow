"""
User registration: oauth-first with local fallback; bootstrap admin provisioning.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.exceptions import UserAlreadyExists
from app.auth.schemas import UserCreate, UserUpdate
from app.auth.user_repository import UserRepository
from app.auth.users import create_user, update_user_password
from app.core.config import settings
from app.models import User
from app.services.oauth_registration_client import (
    OAuthRegisterResult,
    oauth_registration_client,
)

logger = logging.getLogger(__name__)


class RegisterResult:
    __slots__ = ("user_id", "message", "via_oauth", "already_existed")

    def __init__(
        self,
        *,
        user_id: str,
        message: str,
        via_oauth: bool,
        already_existed: bool = False,
    ):
        self.user_id = user_id
        self.message = message
        self.via_oauth = via_oauth
        self.already_existed = already_existed

    def as_response(self) -> dict:
        return {
            "status": "success",
            "message": self.message,
            "user_id": self.user_id,
            "via_oauth": self.via_oauth,
        }


async def _find_local_user(
    session: AsyncSession,
    *,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[User]:
    if user_id:
        try:
            uid = UUID(str(user_id))
            user = await session.get(User, uid)
            if user:
                return user
        except (ValueError, TypeError):
            pass

    if email:
        result = await session.exec(
            select(User).where(func.lower(User.email) == func.lower(email))
        )
        return result.first()

    return None


async def _find_local_user_after_external_write(
    session: AsyncSession,
    *,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    max_attempts: int = 3,
) -> Optional[User]:
    """
    OAuth register commits in another DB connection; end our snapshot before lookup.
    """
    for attempt in range(max_attempts):
        await session.commit()
        user = await _find_local_user(session, user_id=user_id, email=email)
        if user:
            return user
        if attempt < max_attempts - 1:
            await asyncio.sleep(0.15)
    return None


async def _register_via_local(
    session: AsyncSession,
    user: UserCreate,
) -> RegisterResult:
    password = user.password or secrets.token_urlsafe(16)
    try:
        new_user = await create_user(
            session,
            email=user.email,
            password=password,
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        return RegisterResult(
            user_id=str(new_user.id),
            message="User registered successfully",
            via_oauth=False,
        )
    except UserAlreadyExists:
        logger.info("User %s already exists (local register)", user.email)
        existing = await _find_local_user(session, email=user.email)
        if existing:
            return RegisterResult(
                user_id=str(existing.id),
                message="User already exists, skipping registration",
                via_oauth=False,
                already_existed=True,
            )
        logger.error("User %s should exist after UserAlreadyExists", user.email)
        return RegisterResult(
            user_id="",
            message="User already exists, skipping registration",
            via_oauth=False,
            already_existed=True,
        )


async def _register_via_oauth(
    session: AsyncSession,
    user: UserCreate,
) -> Optional[RegisterResult]:
    password = user.password or secrets.token_urlsafe(16)
    login_id = str(user.email)
    oauth_result = oauth_registration_client.register_user(
        user_id=login_id,
        password=password,
        name=login_id.split("@")[0] or login_id,
        email=str(user.email),
    )
    if oauth_result is None:
        return None

    return await _build_result_from_oauth(session, user.email, oauth_result)


async def _build_result_from_oauth(
    session: AsyncSession,
    email: str,
    oauth_result: OAuthRegisterResult,
) -> RegisterResult:
    local = await _find_local_user(
        session,
        user_id=oauth_result.user_id,
        email=email,
    )
    resolved_id = str(local.id) if local else oauth_result.user_id

    if oauth_result.already_existed:
        message = "User already exists, skipping registration"
    else:
        message = "User registered successfully via oauth"

    return RegisterResult(
        user_id=resolved_id,
        message=message,
        via_oauth=True,
        already_existed=oauth_result.already_existed,
    )


async def register_user_account(
    session: AsyncSession,
    user: UserCreate,
) -> RegisterResult:
    """
    Register a user. Uses oauth when OAUTH_REGISTER_ENABLED; falls back to local create.
    """
    if settings.OAUTH_REGISTER_ENABLED:
        oauth_outcome = await _register_via_oauth(session, user)
        if oauth_outcome is not None:
            return oauth_outcome
        logger.warning(
            "OAuth register failed for %s, falling back to local create_user",
            user.email,
        )

    return await _register_via_local(session, user)


async def ensure_admin_user_account(
    session: AsyncSession,
    *,
    email: str,
    password: str,
) -> User:
    """
    Create bootstrap admin. When OAUTH_BOOTSTRAP_VIA_OAUTH=true, register via oauth first,
    sync users.hashed_password to the provided password, then promote to superuser.
    """
    if settings.OAUTH_BOOTSTRAP_VIA_OAUTH:
        oauth_result = oauth_registration_client.register_user(
            user_id=email,
            password=password,
            name="Admin",
            email=email,
        )
        if oauth_result is not None:
            lookup_email = oauth_result.email or email
            admin = await _find_local_user_after_external_write(
                session,
                user_id=oauth_result.user_id,
                email=lookup_email,
            )
            if admin:
                # Sync users.hashed_password with bootstrap form password.
                admin = await update_user_password(session, admin.id, password)
                if not admin.is_superuser:
                    await _promote_to_superuser(session, admin)
                return admin
            logger.error(
                "OAuth bootstrap registered %s (user_id=%s) but users row not found after commit",
                lookup_email,
                oauth_result.user_id,
            )
            raise RuntimeError(
                f"OAuth bootstrap succeeded for {lookup_email} but local users row is missing"
            )

    try:
        return await create_user(
            session,
            email=email,
            password=password,
            is_active=True,
            is_verified=True,
            is_superuser=True,
        )
    except UserAlreadyExists:
        existing = await _find_local_user_after_external_write(session, email=email)
        if existing is None:
            raise
        if not existing.is_superuser:
            await _promote_to_superuser(session, existing)
        return existing


async def _promote_to_superuser(session: AsyncSession, user: User) -> User:
    """Bootstrap-only: align is_superuser after oauth created the row."""
    repo = UserRepository(session)
    return await repo.update(user, UserUpdate(is_superuser=True))

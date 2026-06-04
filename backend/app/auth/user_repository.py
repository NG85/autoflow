"""User persistence and password authentication."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.exceptions import UserAlreadyExists, UserNotExists
from app.auth.password import (
    hash_for_timing_attack_mitigation,
    hash_password,
    verify_and_update_password,
)
from app.auth.schemas import UserCreate, UserUpdate
from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> Optional[User]:
        statement = select(User).where(func.lower(User.email) == func.lower(email))
        result = await self.session.exec(statement)
        return result.first()

    async def create(self, user_create: UserCreate) -> User:
        existing = await self.get_by_email(user_create.email)
        if existing is not None:
            raise UserAlreadyExists()

        user = User(
            email=user_create.email,
            hashed_password=hash_password(user_create.password),
            is_active=user_create.is_active,
            is_superuser=user_create.is_superuser,
            is_verified=user_create.is_verified,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update(self, user: User, user_update: UserUpdate) -> User:
        update_dict = user_update.model_dump(exclude_unset=True)
        if not update_dict:
            return user

        if "email" in update_dict and update_dict["email"] != user.email:
            other = await self.get_by_email(update_dict["email"])
            if other is not None:
                raise UserAlreadyExists()
            user.email = update_dict["email"]
            user.is_verified = False

        if "password" in update_dict and update_dict["password"] is not None:
            user.hashed_password = hash_password(update_dict["password"])

        for field in ("is_active", "is_superuser", "is_verified"):
            if field in update_dict:
                setattr(user, field, update_dict[field])

        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update_password(self, user_id: uuid.UUID, new_password: str) -> User:
        user = await self.get_by_id(user_id)
        if user is None:
            raise UserNotExists(f"User {user_id} does not exist")
        return await self.update(user, UserUpdate(password=new_password))

    async def authenticate(
        self, credentials: OAuth2PasswordRequestForm
    ) -> Optional[User]:
        user = await self.get_by_email(credentials.username)
        if user is None:
            hash_for_timing_attack_mitigation(credentials.password)
            return None

        verified, updated_hash = verify_and_update_password(
            credentials.password, user.hashed_password
        )
        if not verified:
            return None

        if updated_hash is not None:
            user.hashed_password = updated_hash
            self.session.add(user)
            await self.session.commit()
            await self.session.refresh(user)

        return user

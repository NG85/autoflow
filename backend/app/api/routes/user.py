from http import HTTPStatus
import secrets
from fastapi import APIRouter, HTTPException
from fastapi_users.exceptions import UserAlreadyExists
import logging
from sqlmodel import select

from app.api.deps import AsyncSessionDep, CurrentUserDep
from app.auth.schemas import UserCreate, UserRead
from app.auth.users import create_user
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/users/me", response_model=UserRead)
def me(user: CurrentUserDep):
    return user

@router.post("/users/register", response_model=None)
async def register_user(
    user: UserCreate,
    session: AsyncSessionDep
):
    try:
        new_user = await create_user(
            session,
            email=user.email,
            password=user.password or secrets.token_urlsafe(16),
            is_active=True, 
            is_verified=True,
            is_superuser=False,
        )
        return {"status": "success", "message": "User registered successfully", "user_id": str(new_user.id)}
    except UserAlreadyExists:
        logger.info(f"User with email {user.email} already exists, skipping registration")
        # 查询已存在的用户并返回其id
        result = await session.exec(select(User).where(User.email == user.email))
        existing_user = result.first()
        if existing_user:
            return {"status": "success", "message": "User already exists, skipping registration", "user_id": str(existing_user.id)}
        else:
            # 理论上不应该到这里，但以防万一
            logger.error(f"User with email {user.email} should exist but not found")
            return {"status": "success", "message": "User already exists, skipping registration", "user_id": ''}
    except Exception as e:
        logger.error(f"Failed to register user: {e}")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Failed to register user"
        )

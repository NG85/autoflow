from http import HTTPStatus
import secrets
from fastapi import APIRouter, HTTPException
from fastapi_users.exceptions import UserAlreadyExists
import logging

from app.api.deps import AsyncSessionDep, CurrentUserDep
from app.auth.schemas import UserCreate, UserRead
from app.auth.users import create_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/users/me", response_model=UserRead)
def me(user: CurrentUserDep):
    return user

@router.post("/users/register", response_model=UserRead)
async def register_user(
    user: UserCreate,
    session: AsyncSessionDep
):
    try:
        user = await create_user(
            session,
            email=user.email,
            password=user.password or secrets.token_urlsafe(16),
            is_active=True, 
            is_verified=True,
            is_superuser=False,
        )
        return user
    except UserAlreadyExists:
        logger.info(f"User with email {user.email} already exists, skipping registration")
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="User with this email already exists"
        )
    except Exception as e:
        logger.error(f"Failed to register user: {e}")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Failed to register user"
        )

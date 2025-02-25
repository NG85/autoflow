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

@router.post("/users/register", response_model=None)
async def register_user(
    user: UserCreate,
    session: AsyncSessionDep
):
    try:
        await create_user(
            session,
            email=user.email,
            password=user.password or secrets.token_urlsafe(16),
            is_active=True, 
            is_verified=True,
            is_superuser=False,
        )
        return {"status": "success", "message": "User registered successfully"}
    except UserAlreadyExists:
        logger.info(f"User with email {user.email} already exists, skipping registration")
        return {"status": "success", "message": "User already exists, skipping registration"}
    except Exception as e:
        logger.error(f"Failed to register user: {e}")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Failed to register user"
        )

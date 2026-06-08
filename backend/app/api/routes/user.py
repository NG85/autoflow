from http import HTTPStatus
from fastapi import APIRouter, Depends, HTTPException, Request
import logging

from app.api.deps import AsyncSessionDep
from app.auth.registration import register_user_account
from app.auth.schemas import UserCreate, UserRead
from app.auth.users import resolve_user_for_me
from app.core.db import get_db_async_session
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/users/me", response_model=UserRead)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_db_async_session),
):
    user = await resolve_user_for_me(request, session)
    return user


@router.post("/users/register", response_model=None)
async def register_user(user: UserCreate, session: AsyncSessionDep):
    try:
        result = await register_user_account(session, user)
        return result.as_response()
    except Exception as e:
        logger.error("Failed to register user: %s", e, exc_info=True)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Failed to register user",
        )

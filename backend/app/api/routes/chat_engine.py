from fastapi import APIRouter, Depends
from fastapi_pagination import Params, Page

from app.api.deps import SessionDep, CurrentUserDep
from app.repositories import chat_engine_repo
from app.models import ChatEngine

router = APIRouter()


@router.get("/chat-engines")
def list_chat_engines(
    db_session: SessionDep,
    user: CurrentUserDep,
    params: Params = Depends(),
) -> Page[ChatEngine]:
    return chat_engine_repo.paginate(db_session, params)

import logging
from uuid import UUID
from typing import List, Optional, Annotated
from http import HTTPStatus

from pydantic import (
    BaseModel,
    field_validator,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from fastapi_pagination import Params, Page
from app.api.deps import SessionDep, OptionalUserDep, CurrentUserDep
from app.rag.chat.chat_flow import ChatFlow
from app.rag.retrievers.knowledge_graph.schema import KnowledgeGraphRetrievalResult
from app.repositories import chat_repo
from app.models import Chat, ChatUpdate

from app.rag.chat.chat_service import get_final_chat_result
from app.models import Chat, ChatUpdate, ChatFilters
from app.rag.chat.chat_service import (
    user_can_view_chat,
    user_can_edit_chat,
    get_chat_message_subgraph,
    get_chat_message_recommend_questions,
    remove_chat_message_recommend_questions,
)
from app.rag.types import MessageRole, ChatMessage
from app.exceptions import InternalServerError
from app.models.chat import ChatType

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    chat_engine: str = "default"
    chat_id: Optional[UUID] = None
    stream: bool = True
    chat_type: ChatType = ChatType.DEFAULT
    cvg_report: Optional[List[ChatMessage]] = None
    save_only: bool = False
    
    @field_validator("messages")
    @classmethod
    def check_messages(cls, messages: List[ChatMessage]) -> List[ChatMessage]:
        if not messages:
            raise ValueError("messages cannot be empty")
        for m in messages:
            if m.role not in [MessageRole.USER, MessageRole.ASSISTANT]:
                raise ValueError("role must be either 'user' or 'assistant'")
            if not m.content:
                raise ValueError("message content cannot be empty")
            if len(m.content) > 10000:
                raise ValueError("message content cannot exceed 1000 characters")
        if messages[-1].role != MessageRole.USER:
            raise ValueError("last message must be from user")
        return messages
    
    @field_validator("cvg_report")
    @classmethod
    def validate_cvg_report(cls, v: Optional[List[str]], values: dict) -> Optional[List[str]]:
        if v is not None:
            # Ensure the correct chat type
            if values.get("chat_type") != ChatType.CLIENT_VISIT_GUIDE:
                raise ValueError("cvg_report can only be provided when chat_type is CLIENT_VISIT_GUIDE")
                
            # Validate the array length
            if len(v) != 2:
                raise ValueError("cvg_report must contain exactly 2 elements")
            
            # Validate the array elements are not empty
            if not v[0] or not v[1]:
                raise ValueError("cvg_report elements cannot be empty")
            
            # If cvg_report is provided, chat_id is required
            if not values.get("chat_id"):
                raise ValueError("chat_id is required when cvg_report is provided")
        return v
    
    @field_validator("save_only")
    @classmethod
    def validate_save_only(cls, v: bool, values: dict) -> bool:
        if v:
            # save_only can only be used in CLIENT_VISIT_GUIDE mode
            if values.get("chat_type") != ChatType.CLIENT_VISIT_GUIDE:
                raise ValueError("save_only can only be True when chat_type is CLIENT_VISIT_GUIDE")
            # save_only mode requires chat_id
            if not values.get("chat_id"):
                raise ValueError("chat_id is required when save_only is True")
        return v

@router.post("/chats")
def chats(
    request: Request,
    session: SessionDep,
    user: OptionalUserDep,
    chat_request: ChatRequest,
):
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    browser_id = request.state.browser_id

    try:
        chat_flow = ChatFlow(
            db_session=session,
            user=user,
            browser_id=browser_id,
            origin=origin,
            chat_id=chat_request.chat_id,
            chat_messages=chat_request.messages,
            engine_name=chat_request.chat_engine,
            chat_type=chat_request.chat_type,
            cvg_report=chat_request.cvg_report,
            save_only=chat_request.save_only,
        )

        if chat_request.stream:
            return StreamingResponse(
                chat_flow.chat(),
                media_type="text/event-stream",
                headers={
                    "X-Content-Type-Options": "nosniff",
                },
            )
        else:
            return get_final_chat_result(chat_flow.chat())
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/chats")
def list_chats(
    request: Request,
    session: SessionDep,
    user: OptionalUserDep,
    filters: Annotated[ChatFilters, Query()],
    params: Params = Depends(),
) -> Page[Chat]:
    browser_id = request.state.browser_id
    return chat_repo.paginate(session, user, browser_id, filters, params)


@router.get("/chats/{chat_id}")
def get_chat(session: SessionDep, user: OptionalUserDep, chat_id: UUID):
    chat = chat_repo.must_get(session, chat_id)

    if not user_can_view_chat(chat, user):
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail="Access denied")

    return {
        "chat": chat,
        "messages": chat_repo.get_messages(session, chat),
    }


@router.put("/chats/{chat_id}")
def update_chat(
    session: SessionDep, user: CurrentUserDep, chat_id: UUID, chat_update: ChatUpdate
):
    try:
        chat = chat_repo.must_get(session, chat_id)

        if not user_can_edit_chat(chat, user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN, detail="Access denied"
            )

        return chat_repo.update(session, chat, chat_update)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e, exc_info=True)
        raise InternalServerError()


@router.delete("/chats/{chat_id}")
def delete_chat(session: SessionDep, user: CurrentUserDep, chat_id: UUID):
    try:
        chat = chat_repo.must_get(session, chat_id)

        if not user_can_edit_chat(chat, user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN, detail="Access denied"
            )

        return chat_repo.delete(session, chat)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e, exc_info=True)
        raise InternalServerError()


@router.get(
    "/chat-messages/{chat_message_id}/subgraph",
    response_model=KnowledgeGraphRetrievalResult,
)
def get_chat_subgraph(session: SessionDep, user: OptionalUserDep, chat_message_id: int):
    try:
        chat_message = chat_repo.must_get_message(session, chat_message_id)

        if not user_can_view_chat(chat_message.chat, user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN, detail="Access denied"
            )

        result = get_chat_message_subgraph(session, chat_message)
        return result.model_dump(exclude_none=True)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e, exc_info=True)
        raise InternalServerError()


@router.get("/chat-messages/{chat_message_id}/recommended-questions")
def get_recommended_questions(
    session: SessionDep, user: OptionalUserDep, chat_message_id: int
) -> List[str]:
    try:
        chat_message = chat_repo.must_get_message(session, chat_message_id)

        if not user_can_view_chat(chat_message.chat, user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN, detail="Access denied"
            )

        return get_chat_message_recommend_questions(session, chat_message)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e, exc_info=True)
        raise InternalServerError()


@router.post("/chat-messages/{chat_message_id}/recommended-questions")
def refresh_recommended_questions(
    session: SessionDep, user: OptionalUserDep, chat_message_id: int
) -> List[str]:
    try:
        chat_message = chat_repo.must_get_message(session, chat_message_id)

        if not user_can_view_chat(chat_message.chat, user):
            raise HTTPException(
                status_code=HTTPStatus.FORBIDDEN, detail="Access denied"
            )

        remove_chat_message_recommend_questions(session, chat_message_id)

        return get_chat_message_recommend_questions(session, chat_message)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e, exc_info=True)
        raise InternalServerError()

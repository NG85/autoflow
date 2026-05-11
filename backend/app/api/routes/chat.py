import logging
import json
from uuid import UUID
from typing import Dict, List, Optional, Annotated, Iterable, Iterator, Any
from http import HTTPStatus

from pydantic import (
    BaseModel,
    field_validator,
    model_validator,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from fastapi_pagination import Params, Page
from llama_index.core.base.llms.types import ChatMessage, MessageRole

from app.api.deps import SessionDep, OptionalUserDep, CurrentUserDep
from app.rag.chat.chat_flow import ChatFlow
from app.rag.retrievers.knowledge_graph.schema import KnowledgeGraphRetrievalResult
from app.rag.types import ChatEventType
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
from app.exceptions import InternalServerError
from app.models.chat import ChatType
from app.api.routes.models import ChatMode
from app.core.config import settings
from app.services.feishu_billing_facade import (
    BillingScenario,
    check_billing_quota,
    report_billing_usage,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_chat_review_detail(chat_id: Optional[Any]) -> str:
    if chat_id:
        return f"{settings.REVIEW_REPORT_HOST}/c/{chat_id}"
    return f"{settings.REVIEW_REPORT_HOST}/c"


def _check_sia_quota_or_raise() -> None:
    try:
        quota_ok, quota_msg, _ = check_billing_quota()
    except Exception as exc:
        logger.error("SIA quota check failed before /chats: %s", exc)
        raise HTTPException(status_code=502, detail="计费服务异常，请稍后重试")
    if not quota_ok:
        raise HTTPException(status_code=400, detail=quota_msg)


def _report_sia_usage(user: Optional[Any], review_detail: str) -> None:
    report_billing_usage(
        BillingScenario.SIA_CHAT,
        review_detail=review_detail,
        operator_user_id=getattr(user, "id", None) if user else None,
    )


def _extract_chat_id_from_chunk(chunk: Any) -> Optional[str]:
    try:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        # ChatEventType.DATA_PART is encoded as: "2:[{...chat...}]\n"
        for line in text.splitlines():
            if not line.startswith(f"{ChatEventType.DATA_PART.value}:"):
                continue
            payload_str = line.split(":", 1)[1]
            payload = json.loads(payload_str)
            if isinstance(payload, list) and payload:
                first = payload[0] or {}
                chat = first.get("chat") if isinstance(first, dict) else None
                chat_id = chat.get("id") if isinstance(chat, dict) else None
                if chat_id:
                    return str(chat_id)
    except Exception:
        return None
    return None


def _billing_after_stream_complete(
    stream: Iterable[Any],
    user: Optional[Any],
    initial_chat_id: Optional[Any],
) -> Iterator[Any]:
    final_chat_id = str(initial_chat_id) if initial_chat_id else None
    try:
        for chunk in stream:
            parsed_chat_id = _extract_chat_id_from_chunk(chunk)
            if parsed_chat_id:
                final_chat_id = parsed_chat_id
            yield chunk
    except Exception:
        raise
    else:
        _report_sia_usage(user, _build_chat_review_detail(final_chat_id))


class ChatRequest(BaseModel):
    chat_type: ChatType = ChatType.DEFAULT
    messages: List[ChatMessage]
    chat_engine: str = "default"
    dr_enabled: bool = False
    chat_id: Optional[UUID] = None
    stream: bool = True
    chat_mode: ChatMode = ChatMode.DEFAULT
    context: Optional[Dict] = None
    
    @field_validator("messages")
    @classmethod
    def check_messages(cls, messages: List[ChatMessage], values: Dict) -> List[ChatMessage]:
        if not messages:
            raise ValueError("messages cannot be empty")
        for m in messages:
            if m.role not in [MessageRole.USER, MessageRole.ASSISTANT]:
                raise ValueError("role must be either 'user' or 'assistant'")
            if not m.content:
                raise ValueError("message content cannot be empty")
            if len(m.content) > 100000:
                raise ValueError("message content cannot exceed 100000 characters")
        chat_type = getattr(values, "chat_type", None)
        if chat_type == ChatType.DEFAULT and messages[-1].role != MessageRole.USER:
            raise ValueError("last message must be from user")
        return messages
         
    @model_validator(mode="after")
    def validate_chat_mode(self) -> 'ChatRequest':
        if self.chat_type == ChatType.CLIENT_VISIT_GUIDE and self.chat_mode == ChatMode.DEFAULT:
            raise ValueError("chat_mode must be specified when chat_type is CLIENT_VISIT_GUIDE")
        if self.chat_type == ChatType.DEFAULT and self.chat_mode != ChatMode.DEFAULT:
            raise ValueError("chat_mode must be DEFAULT when chat_type is DEFAULT")
   
        if self.chat_mode == ChatMode.SAVE_CVG_REPORT:
            if not self.chat_id:
                raise ValueError("chat_id is required when chat_mode is SAVE_CVG_REPORT")
            if len(self.messages) % 2 != 0:
                raise ValueError("messages must contain even number of elements")
            for i in range(0, len(self.messages), 2):
                if self.messages[i].role != MessageRole.USER or self.messages[i+1].role != MessageRole.ASSISTANT:
                    raise ValueError("messages must contain alternating user and assistant messages")
        return self

    @field_validator("context")
    @classmethod
    def validate_context(cls, context: Optional[Dict]) -> Optional[Dict]:
        if context is None:
            return context

        allowed_keys = {"account_ids", "opportunity_ids"}
        invalid_keys = set(context.keys()) - allowed_keys
        if invalid_keys:
            raise ValueError(f"context can only contain 'account_ids' or 'opportunity_ids', got invalid keys: {invalid_keys}")

        for key in context:
            if not isinstance(context[key], list):
                raise ValueError(f"{key} must be a list")
                
        return context

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
        should_bill_sia = chat_request.chat_mode not in {
            ChatMode.CREATE_CVG_REPORT,
            ChatMode.SAVE_CVG_REPORT,
        }
        if should_bill_sia:
            _check_sia_quota_or_raise()
        incoming_cookie = request.headers.get("cookie")
        if incoming_cookie:
            logger.debug(f"Incoming cookie: {incoming_cookie}")

        chat_flow = ChatFlow(
            db_session=session,
            user=user,
            browser_id=browser_id,
            origin=origin,
            chat_id=chat_request.chat_id,
            chat_messages=chat_request.messages,
            engine_name=chat_request.chat_engine,
            chat_type=chat_request.chat_type,
            chat_mode=chat_request.chat_mode,
            incoming_cookie=incoming_cookie,
            dr_enabled=chat_request.dr_enabled,
            context=chat_request.context,
        )

        if chat_request.stream:
            stream = chat_flow.chat()
            if should_bill_sia:
                stream = _billing_after_stream_complete(
                    stream,
                    user=user,
                    initial_chat_id=chat_request.chat_id,
                )
            return StreamingResponse(
                stream,
                media_type="text/event-stream",
                headers={
                    "X-Content-Type-Options": "nosniff",
                },
            )
        else:
            result = get_final_chat_result(chat_flow.chat())
            if should_bill_sia:
                _report_sia_usage(user, review_detail=_build_chat_review_detail(result.chat_id))
            return result
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

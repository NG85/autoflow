import logging
from uuid import UUID
from typing import Callable, Dict, List, Optional, Annotated, Iterable, Iterator, Any
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
from app.rag.chat.stream_protocol import encode_chat_stream, extract_chat_id_from_stream_item
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

# chat_type=default 时允许的 chat_mode（含三方拜访攻略流程内普通 chat）
_DEFAULT_CHAT_TYPE_MODES = frozenset({
    ChatMode.DEFAULT,
    ChatMode.CVG_CHAT,
})

# chat_type=client_visit_guide 时允许的 chat_mode
_CLIENT_VISIT_GUIDE_CHAT_MODES = frozenset({
    ChatMode.CREATE_CVG_REPORT,
    ChatMode.SAVE_CVG_REPORT,
    ChatMode.CVG_CHAT,
})

# create/save 均不走 SIA 计费
_VISIT_PREP_NO_SIA_MODES = frozenset({
    ChatMode.CREATE_CVG_REPORT,
    ChatMode.SAVE_CVG_REPORT,
})

# 仅生成攻略时做租户额度预检；save 只上报不预检
_VISIT_PREP_QUOTA_MODES = frozenset({
    ChatMode.CREATE_CVG_REPORT,
})


def _should_bill_sia(chat_type: ChatType, chat_mode: ChatMode) -> bool:
    """是否按普通 chat（SIA）计费。

    不计 SIA：
    - create/save_cvg_report（拜访攻略生成/保存）
    - default + cvg_chat（三方拜访攻略流程内的内部 chat）

    计 SIA：
    - default + default、review 等常规问答
    - client_visit_guide + cvg_chat（基于已有攻略报告的问答）
    """
    if chat_mode in _VISIT_PREP_NO_SIA_MODES:
        return False
    if chat_type == ChatType.DEFAULT and chat_mode == ChatMode.CVG_CHAT:
        return False
    return True


def _build_chat_review_detail(chat_id: Optional[Any]) -> str:
    if chat_id:
        return f"{settings.REVIEW_REPORT_HOST}/c/{chat_id}"
    return f"{settings.REVIEW_REPORT_HOST}/c"


def _build_visit_prep_review_detail(chat_id: Optional[Any]) -> str:
    host = settings.REVIEW_REPORT_HOST.rstrip("/")
    if chat_id:
        return f"{host}/agent/sanYeZhi/{chat_id}"
    return f"{host}/agent/clientVisitHistory"


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


def _visit_prep_trace_key(chat_id: Any) -> str:
    return f"client-visit-guide:{chat_id}"


def _check_visit_prep_quota_or_raise() -> None:
    try:
        quota_ok, quota_msg, _ = check_billing_quota()
    except Exception as exc:
        logger.error("Visit prep guide quota check failed before /chats: %s", exc)
        raise HTTPException(status_code=502, detail="计费服务异常，请稍后重试")
    if not quota_ok:
        raise HTTPException(status_code=400, detail=quota_msg)


def _report_visit_prep_usage(
    user: Optional[Any],
    review_detail: str,
    trace_key: str,
) -> None:
    report_billing_usage(
        BillingScenario.ACCOUNT_VISIT_PREP_GUIDE,
        review_detail=review_detail,
        trace_key=trace_key,
        operator_user_id=getattr(user, "id", None) if user else None,
    )


def _resolve_chat_id(
    request_chat_id: Optional[Any],
    stream_chat_id: Optional[str],
) -> Optional[str]:
    """请求体 ``chat_id`` 优先；否则使用流里第一条 ``2:[]`` 解析出的 id。"""
    if request_chat_id:
        return str(request_chat_id)
    return stream_chat_id


def _wrap_billing_stream(
    stream: Iterable[Any],
    request_chat_id: Optional[Any],
    on_stream_success: Callable[[Optional[str]], None],
) -> Iterator[Any]:
    """流式结束后回调；仅在未抛错时上报（与普通聊天一致）。"""
    stream_chat_id: Optional[str] = None
    try:
        for chunk in stream:
            if stream_chat_id is None:
                parsed = extract_chat_id_from_stream_item(chunk)
                if parsed:
                    stream_chat_id = parsed
            yield chunk
    except Exception:
        raise
    else:
        on_stream_success(_resolve_chat_id(request_chat_id, stream_chat_id))


def _billing_after_stream_complete(
    stream: Iterable[Any],
    user: Optional[Any],
    request_chat_id: Optional[Any],
) -> Iterator[Any]:
    return _wrap_billing_stream(
        stream,
        request_chat_id,
        lambda chat_id: _report_sia_usage(user, _build_chat_review_detail(chat_id)),
    )


def _billing_after_visit_prep_stream_complete(
    stream: Iterable[Any],
    user: Optional[Any],
    request_chat_id: Optional[Any],
) -> Iterator[Any]:
    def _on_success(chat_id: Optional[str]) -> None:
        if not chat_id:
            logger.warning("Visit prep billing skipped: no chat_id after successful save stream")
            return
        _report_visit_prep_usage(
            user,
            _build_visit_prep_review_detail(chat_id),
            _visit_prep_trace_key(chat_id),
        )

    return _wrap_billing_stream(stream, request_chat_id, _on_success)


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
        if (
            self.chat_type == ChatType.CLIENT_VISIT_GUIDE
            and self.chat_mode not in _CLIENT_VISIT_GUIDE_CHAT_MODES
        ):
            raise ValueError(
                "chat_mode must be create_cvg_report, save_cvg_report, or cvg_chat "
                "when chat_type is client_visit_guide"
            )
        if (
            self.chat_type == ChatType.DEFAULT
            and self.chat_mode not in _DEFAULT_CHAT_TYPE_MODES
        ):
            raise ValueError(
                "chat_mode must be DEFAULT or CVG_CHAT when chat_type is DEFAULT"
            )

        if (
            self.chat_type == ChatType.CLIENT_VISIT_GUIDE
            and self.chat_mode == ChatMode.CVG_CHAT
            and self.messages[-1].role != MessageRole.USER
        ):
            raise ValueError("last message must be from user")
   
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
        should_bill_sia = _should_bill_sia(
            chat_request.chat_type,
            chat_request.chat_mode,
        )
        needs_visit_prep_quota = (
            chat_request.chat_type == ChatType.CLIENT_VISIT_GUIDE
            and chat_request.chat_mode in _VISIT_PREP_QUOTA_MODES
        )
        should_bill_visit_prep = (
            chat_request.chat_type == ChatType.CLIENT_VISIT_GUIDE
            and chat_request.chat_mode == ChatMode.SAVE_CVG_REPORT
        )
        if should_bill_sia:
            _check_sia_quota_or_raise()
        if needs_visit_prep_quota:
            _check_visit_prep_quota_or_raise()
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
                    request_chat_id=chat_request.chat_id,
                )
            elif should_bill_visit_prep:
                stream = _billing_after_visit_prep_stream_complete(
                    stream,
                    user=user,
                    request_chat_id=chat_request.chat_id,
                )
            return StreamingResponse(
                encode_chat_stream(stream),
                media_type="text/event-stream",
                headers={
                    "X-Content-Type-Options": "nosniff",
                },
            )
        else:
            result = get_final_chat_result(chat_flow.chat())
            chat_id = _resolve_chat_id(
                chat_request.chat_id,
                str(result.chat_id) if result.chat_id else None,
            )
            if should_bill_sia:
                _report_sia_usage(user, review_detail=_build_chat_review_detail(chat_id))
            elif should_bill_visit_prep and chat_id:
                _report_visit_prep_usage(
                    user,
                    review_detail=_build_visit_prep_review_detail(chat_id),
                    trace_key=_visit_prep_trace_key(chat_id),
                )
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

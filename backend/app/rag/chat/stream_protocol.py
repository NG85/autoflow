import json
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

from pydantic import BaseModel

from app.models import ChatMessage, Chat
from app.rag.types import ChatEventType, ChatMessageSate


class ChatStreamPayload:
    def dump(self):
        pass


@dataclass
class ChatStreamDataPayload(ChatStreamPayload):
    chat: Chat
    user_message: ChatMessage
    assistant_message: ChatMessage

    def dump(self):
        return [
            {
                "chat": self.chat.model_dump(mode="json"),
                "user_message": self.user_message.model_dump(mode="json"),
                "assistant_message": self.assistant_message.model_dump(mode="json"),
            }
        ]


@dataclass
class ChatStreamMessagePayload(ChatStreamPayload):
    state: ChatMessageSate = ChatMessageSate.TRACE
    display: str = ""
    context: dict | list | str | BaseModel | None = None
    message: str = ""

    def dump(self):
        if isinstance(self.context, list):
            context = [c.model_dump() for c in self.context]
        elif isinstance(self.context, BaseModel):
            context = self.context.model_dump()
        else:
            context = self.context

        return [
            {
                "state": self.state.name,
                "display": self.display,
                "context": context,
                "message": self.message,
            }
        ]


@dataclass
class ChatEvent:
    event_type: ChatEventType
    payload: str | ChatStreamPayload | None = None

    def encode(self, charset) -> bytes:
        body = self.payload

        if isinstance(body, ChatStreamPayload):
            body = body.dump()

        body = json.dumps(body, separators=(",", ":"))

        return f"{self.event_type.value}:{body}\n".encode(charset)


def _parse_data_part_payload(payload: Any) -> Optional[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    chat = first.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if not chat_id:
        return None
    result: dict[str, Any] = {"chat_id": chat_id}
    assistant_message = first.get("assistant_message")
    if isinstance(assistant_message, dict):
        message_id = assistant_message.get("id")
        if message_id is not None:
            result["message_id"] = message_id
            result["trace"] = assistant_message.get("trace_url")
    return result


def extract_data_part_from_stream_item(item: Any) -> Optional[dict[str, Any]]:
    """Extract chat/message ids from one stream item (ChatEvent or encoded ``2:[...]`` line).

    The chat session id is always present in the first DATA_PART; callers should stop
    after the first successful parse when they only need ``chat_id``.
    """
    if isinstance(item, ChatEvent):
        if item.event_type != ChatEventType.DATA_PART:
            return None
        payload = item.payload
        if not isinstance(payload, ChatStreamDataPayload):
            return None
        return {
            "chat_id": payload.chat.id,
            "message_id": payload.assistant_message.id,
            "trace": payload.assistant_message.trace_url,
        }

    try:
        text = item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item)
    except Exception:
        return None

    prefix = f"{ChatEventType.DATA_PART.value}:"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line.split(":", 1)[1])
        except json.JSONDecodeError:
            continue
        parsed = _parse_data_part_payload(payload)
        if parsed:
            return parsed
    return None


def extract_chat_id_from_stream_item(item: Any) -> Optional[str]:
    data = extract_data_part_from_stream_item(item)
    if not data:
        return None
    return str(data["chat_id"])


def encode_chat_stream(
    generator: Iterable[ChatEvent | str | bytes],
    charset: str = "utf-8",
) -> Iterator[bytes]:
    for item in generator:
        if isinstance(item, ChatEvent):
            yield item.encode(charset)
        elif isinstance(item, bytes):
            yield item
        elif isinstance(item, str):
            yield item.encode(charset)
        else:
            yield str(item).encode(charset)

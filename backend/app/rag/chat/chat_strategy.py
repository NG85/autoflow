from typing import Any, Generator
from app.rag.chat.stream_protocol import ChatEvent

class BaseChatStrategy:
    def __init__(self, chat_flow: Any):
        self.chat_flow = chat_flow

    def execute(self) -> Generator[ChatEvent, None, None]:
        raise NotImplementedError
    
class DefaultChatStrategy(BaseChatStrategy):
    def execute(self) -> Generator[ChatEvent, None, None]:
        # Implement default chat strategy
        pass

class ClientVisitGuideStrategy(BaseChatStrategy):
    def execute(self) -> Generator[ChatEvent, None, None]:
        # Implement client visit guide strategy
        pass

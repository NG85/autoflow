from abc import ABC, abstractmethod
from enum import Enum
from typing import Generator

from app.rag.chat.chat_flow import ChatFlow


class ChatFlowType(str, Enum):
    DEFAULT = "default"
    CLIENT_VISIT_GUIDE = "client_visit_guide"

class ChatFlowStrategy(ABC):
    @abstractmethod
    def execute(self) -> Generator:
        pass

class DefaultFlowStrategy(ChatFlowStrategy):
    def __init__(self, chat_flow: ChatFlow):
        self.chat_flow = chat_flow
        
    def execute(self):
        yield from self.chat_flow._default_chat_flow()

class ClientVisitGuideStrategy(ChatFlowStrategy):
    def __init__(self, chat_flow: ChatFlow):
        self.chat_flow = chat_flow
        
    def execute(self):
        yield from self.chat_flow._client_visit_guide_flow()

import logging

from typing import Any, List, Generator, Optional, Tuple
from langfuse.llama_index._context import langfuse_instrumentor_context

from app.core.config import settings
from app.rag.chat.stream_protocol import (
    ChatStreamMessagePayload,
    ChatEvent,
)
from app.rag.retrievers.knowledge_graph.schema import (
    KnowledgeGraphRetrievalResult,
)
from app.rag.types import (
    ChatMessageSate,
    ChatEventType,
)
from app.utils.jinja2 import get_prompt_by_jinja2_template
from app.rag.chat.chat_flow import ChatFlow
from app.rag.chat.retrieve.retrieve_flow_playbook import PlaybookRetrieveFlow

logger = logging.getLogger(__name__)

class PlaybookQuestionType:
    PLAYBOOK = "playbook"
    GENERAL = "general"

class PlaybookChatFlow(ChatFlow):
    """Specialized chat flow for playbook interactions.
    
    This class extends the base ChatFlow to handle playbook-specific chat logic
    while maintaining compatibility with future base class changes.
    """

    def __init__(self, *args, **kwargs):
        """Initialize with playbook-specific components."""
        super().__init__(*args, **kwargs)
        # Initialize playbook-specific retrieve flow
        self.retrieve_flow = PlaybookRetrieveFlow(
            db_session=self.db_session,
            engine_name=self.engine_name,
            engine_config=self.engine_config,
            llm=self._llm,
            fast_llm=self._fast_llm,
            knowledge_bases=self.knowledge_bases,
        )

    def chat(self) -> Generator[ChatEvent | str, None, None]:
        """Override base chat flow to handle playbook specific logic with fallback"""
        try:
            with self._trace_manager.observe(
                trace_name="PlaybookChatFlow",
                user_id=self.user.email if self.user else f"anonymous-{self.browser_id}",
                metadata={
                    "is_external_engine": self.engine_config.is_external_engine,
                    "chat_engine_config": self.engine_config.screenshot(),
                },
                tags=[f"chat_engine:{self.engine_name}"],
                release=settings.ENVIRONMENT,
            ) as trace:
                trace.update(
                    input={
                        "user_question": self.user_question,
                        "chat_history": self.chat_history,
                    }
                )


                try:
                    if self.engine_config.is_external_engine:
                        yield from self._external_chat()
                    else:
                        response_text, source_documents = yield from self._playbook_chat()
                        trace.update(output=response_text)
                except Exception as playbook_error:
                    # Log playbook specific error
                    logger.warning(
                        "Playbook chat processing failed, falling back to base chat flow",
                        exc_info=playbook_error,
                    )
                    # Fall back to base class chat implementation
                    if self.engine_config.is_external_engine:
                        yield from super()._external_chat()
                    else:
                        response_text, source_documents = yield from super()._builtin_chat()
                        trace.update(output=response_text)

        except Exception as e:
            logger.exception(e)
            yield ChatEvent(
                event_type=ChatEventType.ERROR_PART,
                payload="Encountered an error while processing the chat. Please try again later.",
            )


    def _playbook_chat(
        self,
    ) -> Generator[ChatEvent | str, None, Tuple[Optional[str], List[Any]]]:
        """Playbook specific chat flow implementation"""
        ctx = langfuse_instrumentor_context.get().copy()
        db_user_message, db_assistant_message = yield from self._chat_start()
        langfuse_instrumentor_context.get().update(ctx)

        # 1. Analyze question type to determine if it needs playbook knowledge
        question_type = yield from self._analyze_question_type()
        
        # 2. Search knowledge graph based on question type
        knowledge_graph, knowledge_graph_context = yield from self._search_knowledge_graph(
            user_question=self.user_question,
            question_type=question_type
        )

        # 3. Refine user question with knowledge graph context
        refined_question = yield from self._refine_user_question(
            user_question=self.user_question,
            chat_history=self.chat_history,
            knowledge_graph_context=knowledge_graph_context,
            refined_question_prompt=self.engine_config.llm.condense_question_prompt,
        )

        # 4. Check if clarification needed
        if self.engine_config.clarify_question:
            need_clarify, need_clarify_response = yield from self._clarify_question(
                user_question=refined_question,
                chat_history=self.chat_history,
                knowledge_graph_context=knowledge_graph_context,
            )
            if need_clarify:
                yield from self._chat_finish(
                    db_assistant_message=db_assistant_message,
                    db_user_message=db_user_message,
                    response_text=need_clarify_response,
                    knowledge_graph=knowledge_graph,
                )
                return None, []

        # 5. Search relevant chunks
        relevant_chunks = yield from self._search_relevance_chunks(
            user_question=refined_question
        )

        # 6. Generate answer
        response_text, source_documents = yield from self._generate_answer(
            user_question=refined_question,
            knowledge_graph_context=knowledge_graph_context,
            relevant_chunks=relevant_chunks,
        )

        # 7. Finish chat
        yield from self._chat_finish(
            db_assistant_message=db_assistant_message,
            db_user_message=db_user_message,
            response_text=response_text,
            knowledge_graph=knowledge_graph,
            source_documents=source_documents,
        )

        return response_text, source_documents


    def _analyze_question_type(self) -> Generator[ChatEvent, None, str]:
        """Analyze if the question needs playbook knowledge"""
        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.KG_RETRIEVAL,
                display="Analyzing Question Type",
            ),
        )

        with self._trace_manager.span(
            name="analyze_question_type",
            input={"user_question": self.user_question},
        ) as span:
            question_type = self._fast_llm.predict(
                get_prompt_by_jinja2_template(
                    self.engine_config.llm.analyze_question_type_prompt,
                    question=self.user_question,
                )
            )
            span.end(output={"question_type": question_type})
            return PlaybookQuestionType.PLAYBOOK if question_type.lower() == "true" else PlaybookQuestionType.GENERAL


    def _search_knowledge_graph(
        self,
        user_question: str,
        question_type: str,
    ) -> Generator[ChatEvent, None, Tuple[KnowledgeGraphRetrievalResult, str]]:
        """Enhanced knowledge graph search for playbook"""
        kg_config = self.engine_config.knowledge_graph
        if kg_config is None or kg_config.enabled is False:
            return KnowledgeGraphRetrievalResult(), ""

        with self._trace_manager.span(
            name="search_knowledge_graph",
            input={"user_question": user_question, "question_type": question_type},
        ) as span:
            display_message = (
                "Searching Sales Knowledge Graph for Relevant Context" 
                if question_type == PlaybookQuestionType.PLAYBOOK
                else "Searching Knowledge Graph for Relevant Context"
            )
            
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.KG_RETRIEVAL,
                    display=display_message,
                ),
            )

            search_method = (
                self.retrieve_flow.search_playbook_knowledge_graph 
                if question_type == PlaybookQuestionType.PLAYBOOK
                else self.retrieve_flow.search_knowledge_graph
            )
            knowledge_graph = search_method(user_question)
            knowledge_graph_context = self._get_knowledge_graph_context(knowledge_graph)
            
            span.end(output={
                "knowledge_graph": knowledge_graph,
                "knowledge_graph_context": knowledge_graph_context,
            })

            return knowledge_graph, knowledge_graph_context

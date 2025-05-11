import hashlib
import json
import logging
from datetime import datetime, UTC
import threading
from typing import Dict, List, Optional, Generator, Tuple, Any
from urllib.parse import urljoin
from uuid import UUID

import requests
from langfuse.llama_index import LlamaIndexInstrumentor
from langfuse.llama_index._context import langfuse_instrumentor_context
from llama_index.core import get_response_synthesizer
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.schema import NodeWithScore
from llama_index.core.prompts.rich import RichPromptTemplate

from sqlmodel import Session
from app.core.config import settings
from app.exceptions import ChatNotFound
from app.models import (
    User,
    Chat as DBChat,
    ChatVisibility,
    ChatMessage as DBChatMessage,
)
from app.rag.chat.config import ChatEngineConfig
from app.rag.chat.playbook import QuestionAnalysisResult
from app.rag.chat.retrieve.retrieve_flow import SourceDocument, RetrieveFlow
from app.rag.chat.stream_protocol import (
    ChatEvent,
    ChatStreamDataPayload,
    ChatStreamMessagePayload,
)
from app.rag.llms.dspy import get_dspy_lm_by_llama_llm
from app.rag.retrievers.knowledge_graph.schema import KnowledgeGraphRetrievalResult
from app.rag.types import ChatEventType, ChatMessageSate
from app.rag.utils import parse_goal_response_format
from app.repositories import chat_repo, knowledge_base_repo
from app.site_settings import SiteSetting
from app.utils.tracing import LangfuseContextManager
from app.rag import default_prompt
from app.rag.chat.crm_authority import CRMAuthority, get_user_crm_authority
from app.models.chat import ChatType
from app.api.routes.models import ChatMode

logger = logging.getLogger(__name__)


def parse_chat_messages(
    chat_messages: List[ChatMessage],
) -> tuple[str, List[ChatMessage]]:
    user_question = chat_messages[-1].content
    user_question_args = chat_messages[-1].additional_kwargs
    chat_history = chat_messages[:-1]
    return user_question, user_question_args, chat_history

_EMBEDDING_CACHE:Dict[str, Dict[str, Any]] = {}
_EMBEDDING_LOCK = threading.Lock()

PROMPT_TYPE_MAPPING = {
    "identity_full": default_prompt.IDENTITY_FULL_PROMPT,
    "identity_brief": default_prompt.IDENTITY_BRIEF_PROMPT,
    "capabilities": default_prompt.CAPABILITIES_PROMPT,
    "knowledge_base": default_prompt.KNOWLEDGE_BASE_PROMPT,
    "greeting": default_prompt.IDENTITY_BRIEF_PROMPT,
}

class ChatFlow:
    _trace_manager: LangfuseContextManager

    def __init__(
        self,
        *,
        db_session: Session,
        user: User,
        browser_id: str,
        origin: str,
        chat_messages: List[ChatMessage],
        engine_name: str = "default",
        chat_id: Optional[UUID] = None,
        chat_type: ChatType = ChatType.DEFAULT,
        chat_mode: ChatMode = ChatMode.DEFAULT,
        incoming_cookie: Optional[str] = None,
    ) -> None:
        self.chat_id = chat_id
        self.db_session = db_session
        self.user = user
        self.browser_id = browser_id
        self.engine_name = engine_name
        self.origin = origin
        self.chat_type = chat_type
        self.chat_messages = chat_messages
        self.chat_mode = chat_mode
        self.incoming_cookie = incoming_cookie
        # Load chat engine and chat session.
        self.user_question, self.user_question_args, self.chat_history = parse_chat_messages(chat_messages)
        
        if chat_id:
            # FIXME:
            #   only chat owner or superuser can access the chat,
            #   anonymous user can only access anonymous chat by track_id
            self.db_chat_obj = chat_repo.get(self.db_session, chat_id)
            if not self.db_chat_obj:
                raise ChatNotFound(chat_id)
            try:
                self.engine_config = ChatEngineConfig.load_from_db(
                    db_session, self.db_chat_obj.engine.name
                )
                self.db_chat_engine = self.engine_config.get_db_chat_engine()
            except Exception as e:
                logger.error(f"Failed to load chat engine config: {e}")
                self.engine_config = ChatEngineConfig.load_from_db(
                    db_session, engine_name
                )
                self.db_chat_engine = self.engine_config.get_db_chat_engine()
            logger.info(
                f"Init ChatFlow for chat {chat_id} (chat_engine: {self.db_chat_obj.engine.name})"
            )
            self.chat_history = [
                ChatMessage(role=m.role, content=m.content, additional_kwargs={})
                for m in chat_repo.get_messages(self.db_session, self.db_chat_obj)
            ]
        else:
            self.engine_config = ChatEngineConfig.load_from_db(db_session, engine_name)
            self.db_chat_engine = self.engine_config.get_db_chat_engine()
            self.db_chat_obj = chat_repo.create(
                self.db_session,
                DBChat(
                    # TODO: title should be generated by the LLM
                    title=self.user_question[:100],
                    engine_id=self.db_chat_engine.id,
                    engine_options=self.engine_config.screenshot(),
                    user_id=self.user.id if self.user else None,
                    browser_id=self.browser_id,
                    origin=origin,
                    visibility=(
                        ChatVisibility.PUBLIC
                        if not self.user
                        else ChatVisibility.PRIVATE
                    ),
                    chat_type=self.chat_type
                )
            )
            chat_id = self.db_chat_obj.id
            # slack/discord may create a new chat with history messages
            # now = datetime.now(UTC)
            # for i, m in enumerate(self.chat_history):
            #     chat_repo.create_message(
            #         session=self.db_session,
            #         chat=self.db_chat_obj,
            #         chat_message=DBChatMessage(
            #             role=m.role,
            #             content=m.content,
            #             ordinal=i + 1,
            #             created_at=now,
            #             updated_at=now,
            #             finished_at=now,
            #         ),
            #     )
            if self.chat_history and len(self.chat_history) > 0:
                chat_repo.create_chat_with_messages(
                    session=self.db_session,
                    chat=self.db_chat_obj,
                    messages=self.chat_history,
                )

        # Init Langfuse for tracing.
        enable_langfuse = (
            SiteSetting.langfuse_secret_key and SiteSetting.langfuse_public_key
        )
        instrumentor = LlamaIndexInstrumentor(
            host=SiteSetting.langfuse_host,
            secret_key=SiteSetting.langfuse_secret_key,
            public_key=SiteSetting.langfuse_public_key,
            enabled=enable_langfuse,
        )
        self._trace_manager = LangfuseContextManager(instrumentor)
        
        # Lazy initialization.
        self._llm_initialized = False
        self._kb_initialized = False
        self._retrieve_flow_initialized = False

    # Init LLM.
    def _ensure_llm_initialized(self):
        if not self._llm_initialized:
            self._llm = self.engine_config.get_llama_llm(self.db_session)
            self._fast_llm = self.engine_config.get_fast_llama_llm(self.db_session)
            self._fast_dspy_lm = self.engine_config.get_fast_dspy_lm(self.db_session)
            self._llm_initialized = True

    # Load knowledge bases.
    def _ensure_kb_initialized(self):
        if not self._kb_initialized:
            self.knowledge_bases = self.engine_config.get_knowledge_bases(self.db_session)
            self.knowledge_base_ids = [kb.id for kb in self.knowledge_bases]
            self._kb_initialized = True
            
    # Init retrieve flow.
    def _ensure_retrieve_flow_initialized(self):
        if not self._retrieve_flow_initialized:
            self._ensure_llm_initialized()
            self._ensure_kb_initialized()
            self.retrieve_flow = RetrieveFlow(
                db_session=self.db_session,
                engine_name=self.engine_name,
                engine_config=self.engine_config,
                llm=self._llm,
                fast_llm=self._fast_llm,
                knowledge_bases=self.knowledge_bases,
            )
            self._retrieve_flow_initialized = True
    
    def chat(self) -> Generator[ChatEvent | str, None, None]:
        try:
            # This is the save cvg report command
            if self.chat_type == ChatType.CLIENT_VISIT_GUIDE and self.chat_mode == ChatMode.SAVE_CVG_REPORT:
                yield from self._save_cvg_messages()
                return
            
            with self._trace_manager.observe(
                trace_name="ChatFlow",
                user_id=(
                    self.user.email if self.user else f"anonymous-{self.browser_id}"
                ),
                metadata={
                    "is_external_engine": self.engine_config.is_external_engine,
                    "chat_engine_config": self.engine_config.screenshot(),
                    "chat_type": self.chat_type,
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
                if self.chat_type == ChatType.CLIENT_VISIT_GUIDE and self.chat_mode == ChatMode.CREATE_CVG_REPORT:
                        # This is the create cvg report command
                        data, message = yield from self._generate_client_visit_guide()
                        trace.update(output={
                            "data": data,
                            "message": message,
                        })
                else:
                    if self.engine_config.is_external_engine:
                        yield from self._external_chat()
                    else:
                        response_text, source_documents = yield from self._builtin_chat()
                        trace.update(output=response_text)
        except Exception as e:
            logger.exception(e)
            yield ChatEvent(
                event_type=ChatEventType.ERROR_PART,
                payload="Encountered an error while processing the chat. Please try again later.",
            )

    def _builtin_chat(
        self,
    ) -> Generator[ChatEvent | str, None, Tuple[Optional[str], List[Any]]]:
        ctx = langfuse_instrumentor_context.get().copy()
        db_user_message, db_assistant_message = yield from self._chat_start()
        # Add initialization status display
        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.INITIALIZATION,
                display="Initializing chat session and starting flow",
            ),
        )
        langfuse_instrumentor_context.get().update(ctx)
        
        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.IDENTITY_DETECTION,
                display="Identifying the question's type and routing to the corresponding flow",
            ),
        )
        # 0. Identity question detection and processing
        identity_type = self._detect_identity_question(self.user_question)
        if identity_type:
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.IDENTITY_DETECTION,
                    display=f"Routing to the {identity_type} flow",
                ),
            )
            logger.info(f"Detected identity question of type: {identity_type}")
            identity_response = PROMPT_TYPE_MAPPING.get(identity_type)
            
            with self._trace_manager.span(name="identity_response") as span:
                span.end(output={"identity_type": identity_type, "response_length": len(identity_response)})
                        
            yield from self._chat_finish(
                db_assistant_message=db_assistant_message,
                db_user_message=db_user_message,
                response_text=identity_response,
                source_documents=[]
            )
            
            return identity_response, []
                
        # # TODO: Move competitor knowledge base id to config
        # logger.info(f"Adding competitor knowledge base to retrieve flow")
        # self.backup_knowledge_bases = self.retrieve_flow.knowledge_bases.copy()
        # self.competitor_knowledge_base_id = 240001
        # self.competitor_knowledge_base = knowledge_base_repo.must_get(self.db_session, self.competitor_knowledge_base_id, False)
        # self.retrieve_flow.knowledge_bases.append(self.competitor_knowledge_base)
        
        self.crm_authority = None
        if settings.CRM_ENABLED:
            # 1. Identity verification and permission check step
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.AUTHORIZATION,
                    display="Verifying data access permissions",
                ),
            )
            # Initialize CRM authority control
            self.crm_authority = get_user_crm_authority(
                user_id=self.user.id if self.user else None
            )
            logger.info(f"CRM authority initialized for user {self.user.id if self.user else 'anonymous'}")
        
            if self.user and not self.crm_authority.is_empty():
                # Record user permission statistics information
                auth_stats = {k: len(v) for k, v in self.crm_authority.authorized_items.items()}
                logger.info(f"User {self.user.id} has CRM access: {auth_stats}")
            
        # 2. Retrieve Knowledge graph related to the user question.
        (
            knowledge_graph,
            knowledge_graph_context,
        ) = yield from self._search_knowledge_graph(
            user_question=self.user_question,
            crm_authority=self.crm_authority,
        )

        # 3. Refine the user question using knowledge graph and chat history.
        refined_question = yield from self._refine_user_question(
            user_question=self.user_question,
            chat_history=self.chat_history,
            knowledge_graph_context=knowledge_graph_context,
            refined_question_prompt=self.engine_config.llm.condense_question_prompt,
        )

        # 4. Check if the question provided enough context information or need to clarify.
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
                    source_documents=[],
                )
                return None, []
            
        # # 4. Analyze the user question to determine if it is competitor related.
        # analysis_result = yield from self._analyze_competitor_related(
        #     user_question=self.user_question,
        #     chat_history=self.chat_history,
        #     knowledge_graph_context=knowledge_graph_context,
        # )

        # if not analysis_result.is_competitor_related:
        #     logger.info(f"Restore the original knowledge base since the question is not competitor related")
        #     self.retrieve_flow.knowledge_bases = self.backup_knowledge_bases
             
        # 5. Use refined question to search for relevant chunks.
        relevant_chunks = yield from self._search_relevance_chunks(
            user_question=refined_question,
            crm_authority=self.crm_authority
        )

        # 6. Generate a response using the refined question and related chunks
        response_text, source_documents = yield from self._generate_answer(
            user_question=refined_question,
            knowledge_graph_context=knowledge_graph_context,
            relevant_chunks=relevant_chunks,
        )

        yield from self._chat_finish(
            db_assistant_message=db_assistant_message,
            db_user_message=db_user_message,
            response_text=response_text,
            knowledge_graph=knowledge_graph,
            source_documents=source_documents,
        )

        return response_text, source_documents

    def _chat_start(
            self,
        ) -> Generator[ChatEvent, None, Tuple[DBChatMessage, DBChatMessage]]:
        db_user_message = chat_repo.create_message(
            session=self.db_session,
            chat=self.db_chat_obj,
            chat_message=DBChatMessage(
                role=MessageRole.USER.value,
                trace_url=self._trace_manager.trace_url,
                content=self.user_question.strip(),
            ),
        )
        db_assistant_message = chat_repo.create_message(
            session=self.db_session,
            chat=self.db_chat_obj,
            chat_message=DBChatMessage(
                role=MessageRole.ASSISTANT.value,
                trace_url=self._trace_manager.trace_url,
                content="",
            ),
        )
        yield ChatEvent(
            event_type=ChatEventType.DATA_PART,
            payload=ChatStreamDataPayload(
                chat=self.db_chat_obj,
                user_message=db_user_message,
                assistant_message=db_assistant_message,
            ),
        )
        return db_user_message, db_assistant_message

    def _search_knowledge_graph(
        self,
        user_question: str,
        crm_authority: Optional[CRMAuthority] = None,
        annotation_silent: bool = False,
    ) -> Generator[ChatEvent, None, Tuple[KnowledgeGraphRetrievalResult, str]]:
        kg_config = self.engine_config.knowledge_graph
        if kg_config is None or kg_config.enabled is False:
            return KnowledgeGraphRetrievalResult(), ""

        with self._trace_manager.span(
            name="search_knowledge_graph", input=user_question
        ) as span:
            if not annotation_silent:
                if kg_config.using_intent_search:
                    yield ChatEvent(
                        event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                        payload=ChatStreamMessagePayload(
                            state=ChatMessageSate.KG_RETRIEVAL,
                            display="Identifying the question's intents and performing knowledge graph search",
                        ),
                    )
                else:
                    yield ChatEvent(
                        event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                        payload=ChatStreamMessagePayload(
                            state=ChatMessageSate.KG_RETRIEVAL,
                            display="Searching the knowledge graph for relevant context",
                        ),
                    )


            self._ensure_retrieve_flow_initialized()
            kg_search_gen = self.retrieve_flow.search_knowledge_graph(
                user_question,
                crm_authority=crm_authority
            )
            knowledge_graph = KnowledgeGraphRetrievalResult()
            knowledge_graph_context = ""
            
            try:
                while True:
                    try:
                        stage, message = next(kg_search_gen)
                        if not annotation_silent:
                            display_message = message
                            yield ChatEvent(
                                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                                payload=ChatStreamMessagePayload(
                                    state=stage,
                                    display=display_message,
                                ),
                            )
                    except StopIteration as e:
                        if hasattr(e, 'value'):
                            if isinstance(e.value, KnowledgeGraphRetrievalResult):
                                knowledge_graph = e.value
                                knowledge_graph_context = self.retrieve_flow._get_knowledge_graph_context(knowledge_graph)
                            elif isinstance(e.value, tuple) and len(e.value) == 2:
                                knowledge_graph, knowledge_graph_context = e.value
                        break
            except Exception as e:
                logger.error(f"Error during knowledge graph search: {e}")
                if not annotation_silent:
                    yield ChatEvent(
                        event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                        payload=ChatStreamMessagePayload(
                            state=stage,
                            display=f"Error during knowledge graph search: {str(e)}",
                        ),
                    )
                knowledge_graph = KnowledgeGraphRetrievalResult()
                knowledge_graph_context = ""

            span.end(
                output={
                    "knowledge_graph": knowledge_graph,
                    "knowledge_graph_context": knowledge_graph_context,
                }
            )

        return knowledge_graph, knowledge_graph_context

    def _refine_user_question(
        self,
        user_question: str,
        chat_history: Optional[List[ChatMessage]] = [],
        refined_question_prompt: Optional[str] = None,
        knowledge_graph_context: str = "",
        annotation_silent: bool = False,
    ) -> Generator[ChatEvent, None, str]:
        with self._trace_manager.span(
            name="refine_user_question",
            input={
                "user_question": user_question,
                "chat_history": chat_history,
                "knowledge_graph_context": knowledge_graph_context,
            },
        ) as span:
            if not annotation_silent:
                yield ChatEvent(
                    event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                    payload=ChatStreamMessagePayload(
                        state=ChatMessageSate.REFINE_QUESTION,
                        display="Query rewriting for enhanced information retrieval",
                    ),
                )

            prompt_template = RichPromptTemplate(refined_question_prompt)
            refined_question = self._fast_llm.predict(
                prompt_template,
                graph_knowledges=knowledge_graph_context,
                chat_history=chat_history,
                question=user_question,
                current_date=datetime.now().strftime("%Y-%m-%d"),
            )

            if not annotation_silent:
                yield ChatEvent(
                    event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                    payload=ChatStreamMessagePayload(
                        state=ChatMessageSate.REFINE_QUESTION,
                        message=refined_question,
                    ),
                )

            span.end(output=refined_question)

            return refined_question

    def _clarify_question(
        self,
        user_question: str,
        chat_history: Optional[List[ChatMessage]] = [],
        knowledge_graph_context: str = "",
    ) -> Generator[ChatEvent, None, Tuple[bool, str]]:
        """
        Check if the question clear and provided enough context information, otherwise, it is necessary to
        stop the conversation early and ask the user for the further clarification.

        Args:
            user_question: str
            knowledge_graph_context: str

        Returns:
            bool: Determine whether further clarification of the issue is needed from the user.
            str: The content of the questions that require clarification from the user.
        """
        with self._trace_manager.span(
            name="clarify_question",
            input={
                "user_question": user_question,
                "knowledge_graph_context": knowledge_graph_context,
            },
        ) as span:
            prompt_template = RichPromptTemplate(
                self.engine_config.llm.clarifying_question_prompt
            )

            prediction = self._fast_llm.predict(
                prompt_template,
                graph_knowledges=knowledge_graph_context,
                chat_history=chat_history,
                question=user_question,
            )
            # TODO: using structured output to get the clarity result.
            clarity_result = prediction.strip().strip(".\"'!")
            need_clarify = clarity_result.lower() != "false"
            need_clarify_response = clarity_result if need_clarify else ""

            if need_clarify:
                yield ChatEvent(
                    event_type=ChatEventType.TEXT_PART,
                    payload=need_clarify_response,
                )

            span.end(
                output={
                    "need_clarify": need_clarify,
                    "need_clarify_response": need_clarify_response,
                }
            )

            return need_clarify, need_clarify_response

    def _search_relevance_chunks(
        self, user_question: str, crm_authority: Optional[CRMAuthority] = None
    ) -> Generator[ChatEvent, None, List[NodeWithScore]]:
        with self._trace_manager.span(
            name="search_relevance_chunks", input=user_question
        ) as span:
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.SEARCH_RELATED_DOCUMENTS,
                    display="Retrieving the most relevant documents",
                ),
            )

            relevance_chunks = self.retrieve_flow.search_relevant_chunks(
                user_question,
                crm_authority=crm_authority
            )
            
            span.end(
                output={
                    "relevance_chunks": relevance_chunks,
                }
            )

            return relevance_chunks

    def _generate_answer(
        self,
        user_question: str,
        knowledge_graph_context: str,
        relevant_chunks: List[NodeWithScore],
    ) -> Generator[ChatEvent, None, Tuple[str, List[SourceDocument]]]:
        with self._trace_manager.span(
            name="generate_answer", input=user_question
        ) as span:
            # Use LLM to generate a fallback response if no relevant chunks are found.
            if not relevant_chunks or len(relevant_chunks) == 0:
                fallback_prompt = RichPromptTemplate(default_prompt.FALLBACK_PROMPT)
                no_content_message = self._fast_llm.predict(
                    fallback_prompt,
                    original_question=user_question,
                )
                yield ChatEvent(
                    event_type=ChatEventType.TEXT_PART,
                    payload=no_content_message,
                )
                span.end(
                    output=no_content_message,
                    metadata={
                        "source_documents": [],
                    },
                )
                return no_content_message, []
                
            # Initialize response synthesizer.
            text_qa_template = RichPromptTemplate(
                template_str=self.engine_config.llm.text_qa_prompt
            )
            text_qa_template = text_qa_template.partial_format(
                current_date=datetime.now().strftime("%Y-%m-%d"),
                graph_knowledges=knowledge_graph_context,
                original_question=self.user_question,
            )
            response_synthesizer = get_response_synthesizer(
                llm=self._llm, text_qa_template=text_qa_template, streaming=True
            )

            # Initialize response.
            response = response_synthesizer.synthesize(
                query=user_question,
                nodes=relevant_chunks,
            )
            source_documents = self.retrieve_flow.get_source_documents_from_nodes(
                response.source_nodes
            )
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.SOURCE_NODES,
                    context=source_documents,
                ),
            )

            # Generate response.
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.GENERATE_ANSWER,
                    display="Thinking and generating a precise answer with AI",
                ),
            )
            response_text = ""
            for word in response.response_gen:
                response_text += word
                yield ChatEvent(
                    event_type=ChatEventType.TEXT_PART,
                    payload=word,
                )

            if not response_text:
                raise Exception("Got empty response from LLM")

            span.end(
                output=response_text,
                metadata={
                    "source_documents": source_documents,
                },
            )

            return response_text, source_documents

    def _post_verification(
        self, user_question: str, response_text: str, chat_id: UUID, message_id: int
    ) -> Optional[str]:
        # post verification to external service, will return the post verification result url
        post_verification_url = self.engine_config.post_verification_url
        post_verification_token = self.engine_config.post_verification_token

        if not post_verification_url:
            return None

        external_request_id = f"{chat_id}_{message_id}"
        qa_content = f"User question: {user_question}\n\nAnswer:\n{response_text}"

        with self._trace_manager.span(
            name="post_verification",
            input={
                "external_request_id": external_request_id,
                "qa_content": qa_content,
            },
        ) as span:
            try:
                resp = requests.post(
                    post_verification_url,
                    json={
                        "external_request_id": external_request_id,
                        "qa_content": qa_content,
                    },
                    headers=(
                        {
                            "Authorization": f"Bearer {post_verification_token}",
                        }
                        if post_verification_token
                        else {}
                    ),
                    timeout=10,
                )
                resp.raise_for_status()
                job_id = resp.json()["job_id"]
                post_verification_link = urljoin(
                    f"{post_verification_url}/", str(job_id)
                )

                span.end(
                    output={
                        "post_verification_link": post_verification_link,
                    }
                )

                return post_verification_link
            except Exception as e:
                logger.exception("Failed to post verification: %s", e.message)
                return None

    def _chat_finish(
        self,
        db_assistant_message: ChatMessage,
        db_user_message: ChatMessage,
        response_text: str,
        knowledge_graph: KnowledgeGraphRetrievalResult = KnowledgeGraphRetrievalResult(),
        source_documents: Optional[List[SourceDocument]] = [],
        annotation_silent: bool = False,
    ):
        if not annotation_silent:
            yield ChatEvent(
                event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                payload=ChatStreamMessagePayload(
                    state=ChatMessageSate.FINISHED,
                ),
            )

        post_verification_result_url = self._post_verification(
            self.user_question,
            response_text,
            self.db_chat_obj.id,
            db_assistant_message.id,
        )

        db_assistant_message.sources = [s.model_dump() for s in source_documents]
        db_assistant_message.graph_data = knowledge_graph.to_stored_graph_dict()
        db_assistant_message.content = response_text
        db_assistant_message.post_verification_result_url = post_verification_result_url
        db_assistant_message.updated_at = datetime.now(UTC)
        db_assistant_message.finished_at = datetime.now(UTC)
        self.db_session.add(db_assistant_message)

        db_user_message.graph_data = knowledge_graph.to_stored_graph_dict()
        db_user_message.updated_at = datetime.now(UTC)
        db_user_message.finished_at = datetime.now(UTC)
        self.db_session.add(db_user_message)
        self.db_session.commit()

        yield ChatEvent(
            event_type=ChatEventType.DATA_PART,
            payload=ChatStreamDataPayload(
                chat=self.db_chat_obj,
                user_message=db_user_message,
                assistant_message=db_assistant_message,
            ),
        )

    # TODO: Separate _external_chat() method into another ExternalChatFlow class, but at the same time, we need to
    #  share some common methods through ChatMixin or BaseChatFlow.
    def _external_chat(self) -> Generator[ChatEvent | str, None, None]:
        ctx = langfuse_instrumentor_context.get().copy()
        db_user_message, db_assistant_message = yield from self._chat_start()
        langfuse_instrumentor_context.get().update(ctx)
        self._ensure_retrieve_flow_initialized()
        identity_type = self._detect_identity_question(self.user_question)
        if identity_type:
            logger.info(f"Detected identity question of type: {identity_type}")
            identity_response = PROMPT_TYPE_MAPPING.get(identity_type)
            
            with self._trace_manager.span(name="identity_response") as span:
                span.end(output={"identity_type": identity_type, "response_length": len(identity_response)})
                        
            yield from self._chat_finish(
                db_assistant_message=db_assistant_message,
                db_user_message=db_user_message,
                response_text=identity_response,
                source_documents=[],
            )
            
            return
        
        cache_messages = None
        goal, response_format = self.user_question, {}
        if settings.ENABLE_QUESTION_CACHE and len(self.chat_history) == 0:
            try:
                logger.info(
                    f"start to find_best_answer_for_question with question: {self.user_question}"
                )
                cache_messages = chat_repo.find_best_answer_for_question(
                    self.db_session, self.user_question
                )
                if cache_messages and len(cache_messages) > 0:
                    logger.info(
                        f"find_best_answer_for_question result {len(cache_messages)} for question {self.user_question}"
                    )
            except Exception as e:
                logger.error(
                    f"Failed to find best answer for question {self.user_question}: {e}"
                )

        if not cache_messages or len(cache_messages) == 0:
            try:
                # 1. Generate the goal with the user question, knowledge graph and chat history.
                goal, response_format = yield from self._generate_goal()

                # 2. Check if the goal provided enough context information or need to clarify.
                if self.engine_config.clarify_question:
                    (
                        need_clarify,
                        need_clarify_response,
                    ) = yield from self._clarify_question(
                        user_question=goal, chat_history=self.chat_history
                    )
                    if need_clarify:
                        yield from self._chat_finish(
                            db_assistant_message=db_assistant_message,
                            db_user_message=db_user_message,
                            response_text=need_clarify_response,
                            annotation_silent=True,
                        )
                        return
            except Exception as e:
                goal = self.user_question
                logger.warning(
                    f"Failed to generate refined goal, fallback to use user question as goal directly: {e}",
                    exc_info=True,
                    extra={},
                )

            cache_messages = None
            if settings.ENABLE_QUESTION_CACHE:
                try:
                    logger.info(
                        f"start to find_recent_assistant_messages_by_goal with goal: {goal}, response_format: {response_format}"
                    )
                    cache_messages = chat_repo.find_recent_assistant_messages_by_goal(
                        self.db_session,
                        {"goal": goal, "Lang": response_format.get("Lang", "English")},
                        90,
                    )
                    logger.info(
                        f"find_recent_assistant_messages_by_goal result {len(cache_messages)} for goal {goal}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to find recent assistant messages by goal: {e}"
                    )

        stream_chat_api_url = (
            self.engine_config.external_engine_config.stream_chat_api_url
        )
        if cache_messages and len(cache_messages) > 0:
            stackvm_response_text = cache_messages[0].content
            task_id = cache_messages[0].meta.get("task_id")
            for chunk in stackvm_response_text.split(". "):
                if chunk:
                    if not chunk.endswith("."):
                        chunk += ". "
                    yield ChatEvent(
                        event_type=ChatEventType.TEXT_PART,
                        payload=chunk,
                    )
        else:
            logger.debug(
                f"Chatting with external chat engine (api_url: {stream_chat_api_url}) to answer for user question: {self.user_question}"
            )
            chat_params = {
                "goal": goal,
                "response_format": response_format,
                "namespace_name": "Default",
            }
            res = requests.post(stream_chat_api_url, json=chat_params, stream=True)

            # Notice: External type chat engine doesn't support non-streaming mode for now.
            stackvm_response_text = ""
            task_id = None
            for line in res.iter_lines():
                if not line:
                    continue

                # Append to final response text.
                chunk = line.decode("utf-8")
                if chunk.startswith("0:"):
                    word = json.loads(chunk[2:])
                    stackvm_response_text += word
                    yield ChatEvent(
                        event_type=ChatEventType.TEXT_PART,
                        payload=word,
                    )
                else:
                    yield line + b"\n"

                try:
                    if chunk.startswith("8:") and task_id is None:
                        states = json.loads(chunk[2:])
                        if len(states) > 0:
                            # accesss task by http://endpoint/?task_id=$task_id
                            task_id = states[0].get("task_id")
                except Exception as e:
                    logger.error(f"Failed to get task_id from chunk: {e}")

        response_text = stackvm_response_text
        base_url = stream_chat_api_url.replace("/api/stream_execute_vm", "")
        try:
            post_verification_result_url = self._post_verification(
                goal,
                response_text,
                self.db_chat_obj.id,
                db_assistant_message.id,
            )
            db_assistant_message.post_verification_result_url = (
                post_verification_result_url
            )
        except Exception:
            logger.error(
                "Specific error occurred during post verification job.", exc_info=True
            )

        trace_url = f"{base_url}?task_id={task_id}" if task_id else ""
        message_meta = {
            "task_id": task_id,
            "goal": goal,
            **response_format,
        }

        db_assistant_message.content = response_text
        db_assistant_message.trace_url = trace_url
        db_assistant_message.meta = message_meta
        db_assistant_message.updated_at = datetime.now(UTC)
        db_assistant_message.finished_at = datetime.now(UTC)
        self.db_session.add(db_assistant_message)

        db_user_message.trace_url = trace_url
        db_user_message.meta = message_meta
        db_user_message.updated_at = datetime.now(UTC)
        db_user_message.finished_at = datetime.now(UTC)
        self.db_session.add(db_user_message)
        self.db_session.commit()

        yield ChatEvent(
            event_type=ChatEventType.DATA_PART,
            payload=ChatStreamDataPayload(
                chat=self.db_chat_obj,
                user_message=db_user_message,
                assistant_message=db_assistant_message,
            ),
        )

    def _generate_goal(self) -> Generator[ChatEvent, None, Tuple[str, dict]]:
        try:
            refined_question = yield from self._refine_user_question(
                user_question=self.user_question,
                chat_history=self.chat_history,
                refined_question_prompt=self.engine_config.llm.generate_goal_prompt,
                annotation_silent=True,
            )

            goal = refined_question.strip()
            if goal.startswith("Goal: "):
                goal = goal[len("Goal: ") :].strip()
        except Exception as e:
            logger.error(f"Failed to refine question with related knowledge graph: {e}")
            goal = self.user_question

        response_format = {}
        try:
            clean_goal, response_format = parse_goal_response_format(goal)
            logger.info(f"clean goal: {clean_goal}, response_format: {response_format}")
            if clean_goal:
                goal = clean_goal
        except Exception as e:
            logger.error(f"Failed to parse goal and response format: {e}")

        return goal, response_format


    def _analyze_competitor_related(self,
        user_question: str,
        chat_history: Optional[List[ChatMessage]] = list,
        knowledge_graph_context: str = "",
        annotation_silent: bool = False,
    ) -> Generator[ChatEvent, None, QuestionAnalysisResult]:
        """Analyze if the question is competitor related"""
        with self._trace_manager.span(
            name="_analyze_competitor_related",
            input={
                "user_question": user_question,
                "chat_history": chat_history,
                "knowledge_graph_context": knowledge_graph_context,
            },
        ) as span:
            
            if not annotation_silent:
                yield ChatEvent(
                    event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
                    payload=ChatStreamMessagePayload(
                        state=ChatMessageSate.ANALYZE_COMPETITOR_RELATED,
                        display="Analyzing If Question is Competitor Related",
                    ),
                )

            # Analyze the question
            prompt_template = self.engine_config.llm.analyze_competitor_related_prompt
            analysis_result = self._fast_llm.predict(
                prompt_template,
                question=user_question,
                chat_history=chat_history,
                current_date=datetime.now().strftime("%Y-%m-%d"),
            )
                       
            try:
                logger.info(f"Question analysis result: {analysis_result}")
                json_str = self._extract_json_from_markdown(analysis_result)
                result = QuestionAnalysisResult.model_validate_json(json_str)
            except Exception as e:
                logger.error(f"Failed to parse question analysis result: {e}")
                # Return default result
                result = QuestionAnalysisResult(
                    is_competitor_related=False,
                    competitor_focus="",
                    competitor_names=[],
                    comparison_aspects=[],
                    needs_technical_details=False,
                )
                
            span.end(output=result.model_dump())
            return result
        
    def _extract_json_from_markdown(self, text: str) -> str:
        """Extract JSON content from markdown code blocks or plain text"""
        import re
        
        # Try to extract JSON from markdown code block
        code_block_pattern = r"```(?:json)?\n([\s\S]*?)\n```"
        if match := re.search(code_block_pattern, text):
            return match.group(1).strip()
            
        # If no code block found, try to find JSON-like content
        json_pattern = r"\{[\s\S]*\}"
        if match := re.search(json_pattern, text):
            return match.group(0).strip()
            
        # If no JSON-like content found, return the original text
        return text.strip()
    
    def _detect_identity_question(self, user_question: str) -> str:
        """
        Use embedding search to detect if the question is about the assistant's identity and capabilities, 
        and return the specific type if so
        
        Returns:
            str: "identity_full", "identity_brief", "capabilities", "knowledge_base", "greeting" or None
        """
        # 1. Use embedding search to check for similar identity questions
        try:
            # Get embedding model directly from the session
            from app.rag.embeddings.resolver import get_default_embed_model
            embed_model = get_default_embed_model(self.db_session)
            
            if not embed_model:
                # If no embedding model is available, fall back to LLM detection
                logger.warning("No embedding model available for identity detection, falling back to LLM")
                raise ValueError("No embedding model available")
            
            # Get embedding for user question
            user_question_embedding = embed_model.get_query_embedding(user_question)
            
            # Predefined identity questions with their categories
            identity_questions = {
                # Full identity questions
                "Tell me more about yourself": "identity_full",
                "介绍一下你自己": "identity_full",
                "详细介绍一下你": "identity_full",
                "你是什么": "identity_full",
                "请介绍一下你自己": "identity_full",
                "tell me about yourself": "identity_full",
                
                # Brief identity questions
                "Who are you?": "identity_brief",
                "你是谁": "identity_brief",
                "你叫什么名字": "identity_brief",
                "你叫什么": "identity_brief",
                "你叫啥": "identity_brief",
                "what is your name": "identity_brief",
                
                # Capability questions
                "What can you do?": "capabilities",
                "你能做什么": "capabilities",
                "你能帮我什么": "capabilities",
                "你能帮我干啥": "capabilities",
                "你有什么功能": "capabilities",
                "你的能力": "capabilities",
                "what can you do": "capabilities",
                "你能帮我做什么": "capabilities",
                "你能干啥": "capabilities",
                "你会干嘛": "capabilities", 
                "你有啥用": "capabilities",
                "你能干什么": "capabilities",
                "你会做什么": "capabilities",
                "你能帮我干嘛": "capabilities",
                "你能为我干啥": "capabilities",
                "你能为我做什么": "capabilities",
                "你对我有什么用": "capabilities",
                "你怎么帮我": "capabilities",
                "你能帮到我什么": "capabilities",
                "你有什么用": "capabilities",
                "你能提供什么服务": "capabilities",
                "你的职责是什么": "capabilities",
                    
                # Knowledge base questions
                "Are you just a knowledge base?": "knowledge_base",
                "你只是一个知识库吗": "knowledge_base",
                "你是个知识库吗": "knowledge_base",
                "你是知识库吗": "knowledge_base",
                "你只是知识库吗": "knowledge_base",
                "你是搜索工具吗": "knowledge_base",
                "你跟知识库有什么区别": "knowledge_base",
                "你跟知识库有什么不同": "knowledge_base",
                "你跟知识库有什么不一样": "knowledge_base",
                "difference between you and knowledge base": "knowledge_base",
                "your difference with knowledge base": "knowledge_base",

                # Greeting questions
                "你好": "greeting",
                "hello": "greeting",
                "hi": "greeting",
                "hey": "greeting",
                "嗨": "greeting",
                "哈喽": "greeting",
                "早上好": "greeting",
                "下午好": "greeting",
                "晚上好": "greeting",
                "good morning": "greeting",
                "good afternoon": "greeting",
                "good evening": "greeting",
            }
            
            global _EMBEDDING_CACHE, _EMBEDDING_LOCK
            cache_key = hashlib.md5(json.dumps(sorted(identity_questions.keys())).encode()).hexdigest()
            
            with _EMBEDDING_LOCK:
                if cache_key not in _EMBEDDING_CACHE:
                    # Calculate embeddings for predefined questions and cache them
                    questions = list(identity_questions.keys())
                    batch_embeddings = embed_model.get_text_embedding_batch(questions)

                    embeddings = {
                        q: {
                            'embedding': batch_embeddings[i],
                            'category': identity_questions[q]
                        }
                        for i, q in enumerate(questions)
                    }
                    _EMBEDDING_CACHE[cache_key] = embeddings
                    logger.info(f"Generated new embeddings cache: {cache_key}")
            
            self._identity_question_embeddings = _EMBEDDING_CACHE[cache_key]
            
            # Find the closest match using cosine similarity
            import numpy as np
            best_similarity = -1
            best_category = None
            
            # Pre-calculate the norm of the user question
            user_norm = np.linalg.norm(user_question_embedding)
            
            # for question, data in self._identity_question_embeddings.items():
            #     # Calculate cosine similarity (1 - cosine_distance)
            #     similarity = np.dot(user_question_embedding, data['embedding']) / (user_norm * np.linalg.norm(data['embedding']))
                
            #     if similarity > best_similarity:
            #         best_similarity = similarity
            #         best_category = data['category']
            
            
            # Vectorized calculation (faster than loop)
            if not hasattr(self, '_identity_emb_matrix'):
                embeddings = [data['embedding'] for data in self._identity_question_embeddings.values()]
                self._identity_emb_matrix = np.array(embeddings)
                self._identity_categories = [data['category'] for data in self._identity_question_embeddings.values()]
                # Pre-calculate norms and store as column vector
                self._identity_norms = np.linalg.norm(self._identity_emb_matrix, axis=1, keepdims=True)

            user_emb = np.array(user_question_embedding).reshape(1, -1)
            dot_products = np.dot(self._identity_emb_matrix, user_emb.T)
            similarities = dot_products / (self._identity_norms * user_norm)

            # Find the maximum similarity index
            max_index = np.argmax(similarities)
            best_similarity = similarities[max_index][0]
            best_category = self._identity_categories[max_index]
            
            # Use a threshold to determine if it's a match
            similarity_threshold = settings.EMBEDDING_THRESHOLD  # Adjust based on testing
            logger.info(f"Best category: {best_category}, similarity: {best_similarity:.4f}")
            if best_similarity >= similarity_threshold:
                logger.info(f"Embedding identity detection for '{user_question}': {best_category} (similarity: {best_similarity:.4f})")
                return best_category
            
            # If no strong match found, proceed to LLM detection
            logger.info(f"No strong embedding match found for '{user_question}' (best: {best_category}, similarity: {best_similarity:.4f})")
        except Exception as e:
            logger.error(f"Error in embedding identity detection: {e}")
        
        # 2. Return None if no match is found, indicating the question is not about the assistant's identity
        return None

    def _save_cvg_messages(self) -> Generator[ChatEvent | str, None, None]:
        """Save user command and cvg report as chat messages"""                    
        try:
            db_messages = []
            now = datetime.now(UTC)
            for message in self.chat_messages:
                db_message = DBChatMessage(
                    chat_id=self.db_chat_obj.id,
                    role=message.role,
                    content=message.content,
                    user_id=self.user.id if self.user else None,
                    meta=message.additional_kwargs if message.additional_kwargs else {},
                    created_at=now,
                    updated_at=now,
                    finished_at=now
                )
                db_messages.append(db_message)
            
            self.db_chat_obj, messages = chat_repo.create_chat_with_messages(
                self.db_session,
                self.db_chat_obj,
                db_messages
            )
            
            yield ChatEvent(
                event_type=ChatEventType.DATA_PART,
                payload=ChatStreamDataPayload(
                    chat=self.db_chat_obj,
                    user_message=db_messages[-2],
                    assistant_message=db_messages[-1],
                ),
            )
        except Exception as e:
            logger.error(f"Failed to save cvg messages: {e}")
            raise
        
    
    # TECHDEBT: Refactor to independent strategy class.
    def _generate_client_visit_guide(self) -> Generator[ChatEvent | str, None, Tuple[Optional[dict], Optional[str]]]:
        """Invoke external service to generate client visit guide"""
        db_user_message, db_assistant_message = yield from self._chat_start()
        # Add initialization status display
        yield ChatEvent(
            event_type=ChatEventType.MESSAGE_ANNOTATIONS_PART,
            payload=ChatStreamMessagePayload(
                state=ChatMessageSate.INITIALIZATION,
                display="Initializing chat session and starting generation flow",
            ),
        )
        ctx = langfuse_instrumentor_context.get().copy()
        langfuse_instrumentor_context.get().update(ctx)
    
        # Invoke external document generation service
        aldebaran_cvgg_url = settings.ALDEBARAN_CVGG_URL
        # Build request body
        payload = {
            "account_name": self.user_question_args.get("account_name", ""),
            "account_id": self.user_question_args.get("account_id", ""),
            "content": self.user_question,
            "tenant_id": settings.ALDEBARAN_TENANT_ID,
        }
        response = requests.post(aldebaran_cvgg_url, json=payload, timeout=300, headers={"cookie": self.incoming_cookie})
        data = None
        error_message = None
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error occurred: {e}")
            error_message = "报告服务响应异常，请稍后再试"
                
        if error_message:
            with self._trace_manager.span(name="client_visit_guide_generation") as span:
                span.end(output=error_message)
             
            yield from self._chat_finish(
                db_assistant_message=db_assistant_message,
                db_user_message=db_user_message,
                response_text=error_message,
                source_documents=[]
            )
        else:
            # Parse JSON response
            result = response.json()
            data = result["data"]
            with self._trace_manager.span(name="client_visit_guide_generation") as span:
                span.end(output={json.dumps(data)})
                        
            yield from self._chat_finish(
                db_assistant_message=db_assistant_message,
                db_user_message=db_user_message,
                response_text=json.dumps(data),
                source_documents=[]
            )
            
        return data, error_message

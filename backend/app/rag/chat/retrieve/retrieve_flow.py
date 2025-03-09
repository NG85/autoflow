import logging
from datetime import datetime
from typing import Generator, List, Optional, Tuple

from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.llms import LLM
from llama_index.core.schema import NodeWithScore, QueryBundle
from pydantic import BaseModel
from sqlmodel import Session

from app.models import (
    Document as DBDocument,
    KnowledgeBase,
)
from app.utils.jinja2 import get_prompt_by_jinja2_template
from app.rag.chat.config import ChatEngineConfig
from app.rag.retrievers.knowledge_graph.fusion_retriever import (
    KnowledgeGraphFusionRetriever,
)
from app.rag.retrievers.knowledge_graph.schema import (
    KnowledgeGraphRetrievalResult,
    KnowledgeGraphRetrieverConfig,
)
from app.rag.retrievers.chunk.fusion_retriever import ChunkFusionRetriever
from app.repositories import document_repo
from app.rag.types import ChatMessageSate

dispatcher = get_dispatcher(__name__)
logger = logging.getLogger(__name__)


class SourceDocument(BaseModel):
    id: int
    name: str
    source_uri: Optional[str] = None


class RetrieveFlow:
    def __init__(
        self,
        db_session: Session,
        engine_name: str = "default",
        engine_config: Optional[ChatEngineConfig] = None,
        llm: Optional[LLM] = None,
        fast_llm: Optional[LLM] = None,
        knowledge_bases: Optional[List[KnowledgeBase]] = None,
    ):
        self.db_session = db_session
        self.engine_name = engine_name
        self.engine_config = engine_config or ChatEngineConfig.load_from_db(
            db_session, engine_name
        )
        self.db_chat_engine = self.engine_config.get_db_chat_engine()

        # Init LLM.
        self._llm = llm or self.engine_config.get_llama_llm(self.db_session)
        self._fast_llm = fast_llm or self.engine_config.get_fast_llama_llm(
            self.db_session
        )

        # Load knowledge bases.
        self.knowledge_bases = (
            knowledge_bases or self.engine_config.get_knowledge_bases(self.db_session)
        )
        self.knowledge_base_ids = [kb.id for kb in self.knowledge_bases]

    def retrieve(self, user_question: str) -> List[NodeWithScore]:
        if self.engine_config.refine_question_with_kg:
            # 1. Retrieve Knowledge graph related to the user question.
            _, knowledge_graph_context = self.search_knowledge_graph(user_question)

            # 2. Refine the user question using knowledge graph and chat history.
            self._refine_user_question(user_question, knowledge_graph_context)

        # 3. Search relevant chunks based on the user question.
        return self.search_relevant_chunks(user_question=user_question)

    def retrieve_documents(self, user_question: str) -> List[DBDocument]:
        nodes = self.retrieve(user_question)
        return self.get_documents_from_nodes(nodes)

    def search_knowledge_graph(
        self, user_question: str
    ) -> Generator[Tuple[ChatMessageSate, str], None, Tuple[KnowledgeGraphRetrievalResult, str]]:
        kg_config = self.engine_config.knowledge_graph
        knowledge_graph = KnowledgeGraphRetrievalResult()
        knowledge_graph_context = ""
        if kg_config is not None and kg_config.enabled:
            kg_retriever = KnowledgeGraphFusionRetriever(
                db_session=self.db_session,
                knowledge_base_ids=[kb.id for kb in self.knowledge_bases],
                llm=self._llm,
                use_query_decompose=kg_config.using_intent_search,
                use_async=True,
                config=KnowledgeGraphRetrieverConfig.model_validate(
                    kg_config.model_dump(exclude={"enabled", "using_intent_search"})
                ),
            )
            try:
                kg_gen = kg_retriever.retrieve_knowledge_graph(user_question)
                
                try:
                    while True:
                        try:
                            stage, message = next(kg_gen)
                            yield (stage, message)
                        except StopIteration as e:
                            if hasattr(e, 'value'):
                                knowledge_graph = e.value
                            break
                except Exception as e:
                    logger.error(f"Knowledge graph retrieval process error: {e}")
                    yield (ChatMessageSate.KG_RETRIEVAL, f"Knowledge graph retrieval failed: {str(e)}")
                    knowledge_graph = KnowledgeGraphRetrievalResult()
                
                # Convert the knowledge graph to context text
                knowledge_graph_context = self._get_knowledge_graph_context(knowledge_graph)
                yield (ChatMessageSate.KG_RETRIEVAL, "Organizing and processing knowledge graph information")

            except Exception as e:
                logger.error(f"Error in knowledge graph search: {e}")
                yield (ChatMessageSate.KG_RETRIEVAL, f"Error during knowledge graph search: {str(e)}")
        else:
                yield (ChatMessageSate.KG_RETRIEVAL, "Knowledge graph search is disabled, skip it")
        return knowledge_graph, knowledge_graph_context

    def _get_knowledge_graph_context(
        self, knowledge_graph: KnowledgeGraphRetrievalResult
    ) -> str:
        if self.engine_config.knowledge_graph.using_intent_search:
            kg_context_template = get_prompt_by_jinja2_template(
                self.engine_config.llm.intent_graph_knowledge,
                # For forward compatibility considerations.
                sub_queries=knowledge_graph.to_subqueries_dict(),
            )
            return kg_context_template.template
        else:
            kg_context_template = get_prompt_by_jinja2_template(
                self.engine_config.llm.normal_graph_knowledge,
                entities=knowledge_graph.entities,
                relationships=knowledge_graph.relationships,
            )
            return kg_context_template.template

    def _refine_user_question(
        self, user_question: str, knowledge_graph_context: str
    ) -> str:
        return self._fast_llm.predict(
            get_prompt_by_jinja2_template(
                self.engine_config.llm.condense_question_prompt,
                graph_knowledges=knowledge_graph_context,
                question=user_question,
                current_date=datetime.now().strftime("%Y-%m-%d"),
            ),
        )

    def search_relevant_chunks(self, user_question: str) -> List[NodeWithScore]:
        retriever = ChunkFusionRetriever(
            db_session=self.db_session,
            knowledge_base_ids=self.knowledge_base_ids,
            llm=self._llm,
            config=self.engine_config.vector_search,
            use_query_decompose=False,
            use_async=True,
        )
        return retriever.retrieve(QueryBundle(user_question))

    def get_documents_from_nodes(self, nodes: List[NodeWithScore]) -> List[DBDocument]:
        document_ids = [n.node.metadata["document_id"] for n in nodes]
        documents = document_repo.fetch_by_ids(self.db_session, document_ids)
        # Keep the original order of document ids, which is sorted by similarity.
        return sorted(documents, key=lambda x: document_ids.index(x.id))

    def get_source_documents_from_nodes(
        self, nodes: List[NodeWithScore]
    ) -> List[SourceDocument]:
        documents = self.get_documents_from_nodes(nodes)
        return [
            SourceDocument(
                id=doc.id,
                name=doc.name,
                source_uri=doc.source_uri,
            )
            for doc in documents
        ]

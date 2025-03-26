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
from app.rag.types import ChatMessageSate, CrmDataType
from app.rag.chat.crm_authority import (
    CRMAuthority,
    get_crm_type,
    identify_crm_entity_type,
    is_crm_category,
)

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
        self, user_question: str, crm_authority: Optional[CRMAuthority] = None
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

                # Filter the knowledge graph results according to the CRM authority
                if crm_authority and not crm_authority.is_empty():
                    # Record the number of entities and their types before filtering
                    if logger.isEnabledFor(logging.DEBUG):
                        category_counts = {}
                        for entity in knowledge_graph.entities:
                            logger.debug(f"Retrieved entity before filtering: {entity}")
                            meta = getattr(entity, "meta", {}) or {}
                            category = meta.get("category", "unknown")
                            category_counts[category] = category_counts.get(category, 0) + 1
                        logger.debug(f"Entity categories before filtering: {category_counts}")
                    
                    knowledge_graph = self.filter_knowledge_graph_by_authority(knowledge_graph, crm_authority)
                
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

    def search_relevant_chunks(self, user_question: str, crm_authority: Optional[CRMAuthority] = None) -> List[NodeWithScore]:
        retriever = ChunkFusionRetriever(
            db_session=self.db_session,
            knowledge_base_ids=self.knowledge_base_ids,
            llm=self._llm,
            config=self.engine_config.vector_search,
            use_query_decompose=False,
            use_async=True,
        )
        nodes = retriever.retrieve(QueryBundle(user_question))

        # Filter the nodes according to the CRM authority
        if crm_authority and not crm_authority.is_empty():
            nodes = self.filter_chunks_by_authority(nodes, crm_authority)
        
        return nodes
                

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


    def filter_knowledge_graph_by_authority(
        self,
        knowledge_graph: KnowledgeGraphRetrievalResult,
        crm_authority: CRMAuthority
    ) -> KnowledgeGraphRetrievalResult:
        """Filter the knowledge graph results according to the CRM authority"""
        if not crm_authority or crm_authority.is_empty():
            return knowledge_graph
            
        filtered_entities = []
        filtered_relationships = []
                
        # Filter entities
        for entity in knowledge_graph.entities:
            is_authorized = True
              
            # Identify the CRM entity type and ID (only return the information of CRM type entities)
            entity_type, entity_id = identify_crm_entity_type(entity)

            # Only process CRM related entities
            if entity_type and entity_id:
                if not crm_authority.is_authorized(entity_type, entity_id):
                    is_authorized = False
                    logger.debug(f"Filtering out unauthorized {entity_type.value} entity: {entity_id}")
               
            # Retain non-CRM entities or entities with permission
            if is_authorized:
                filtered_entities.append(entity)
        
        # Filter relationships
        for rel in knowledge_graph.relationships:
            is_authorized = True
            meta = getattr(rel, "meta", {}) or {}
            
            # Only check the relationships of CRM type
            category = meta.get("category")
            
            # Only check the CRM type relationships
            if is_crm_category(category):
                doc_id = meta.get("document_id")
                if doc_id:
                    # Get the document metadata to check the permission
                    document = document_repo.get(self.db_session, doc_id)
                    if document and document.meta:
                        doc_meta = document.meta
                        
                        # Check the opportunity ID permission
                        opportunity_id = doc_meta.get("unique_id") or doc_meta.get("opportunity_id")
                        if opportunity_id and not crm_authority.is_authorized(CrmDataType.OPPORTUNITY, opportunity_id):
                            is_authorized = False
                        
                        # Check the account ID permission
                        if "account" in doc_meta and isinstance(doc_meta["account"], dict):
                            account_id = doc_meta["account"].get("account_id") or doc_meta["account"].get("unique_id")
                            if account_id and not crm_authority.is_authorized(CrmDataType.ACCOUNT, account_id):
                                is_authorized = False
                
                # Check if the relationship metadata directly contains CRM ID
                opportunity_id = meta.get("opportunity_id")
                if opportunity_id and not crm_authority.is_authorized(CrmDataType.OPPORTUNITY, opportunity_id):
                    is_authorized = False
                    
                account_id = meta.get("account_id")
                if account_id and not crm_authority.is_authorized(CrmDataType.ACCOUNT, account_id):
                    is_authorized = False
            
            # Retain non-CRM relationships or relationships with permission
            if is_authorized:
                filtered_relationships.append(rel)
        
        # Update the filtered knowledge graph
        result = knowledge_graph.model_copy()
        result.entities = filtered_entities
        result.relationships = filtered_relationships
        
        logger.info(f"Applied CRM authority filter on knowledge graph: {len(filtered_entities)}/{len(knowledge_graph.entities)} entities, "
                    f"{len(filtered_relationships)}/{len(knowledge_graph.relationships)} relationships kept")
        
        return result
    
    
    def filter_chunks_by_authority(
        self,
        nodes: List[NodeWithScore],
        crm_authority: CRMAuthority
    ) -> List[NodeWithScore]:
        """Filter the document chunks results according to the CRM authority"""
        if not crm_authority or crm_authority.is_empty():
            return nodes
            
        filtered_nodes = []
        
        for node in nodes:
            is_authorized = True
        
            logger.debug(f"Filtering chunks by CRM authority: {crm_authority}, node: {node}")
            # Check the node metadata
            if hasattr(node, "metadata") and node.metadata:
                logger.debug(f"Node metadata before filtering: {node.metadata}")
                meta = node.metadata
                
                # Only process the documents with the CRM category mark
                node_category = meta.get("category")
                crm_type = get_crm_type(node_category)
                
                # Check the permission of the CRM type
                if crm_type:
                    id_field_map = {
                        CrmDataType.CRM: ["unique_id", "opportunity_id", "account_id"],
                        CrmDataType.OPPORTUNITY: ["opportunity_id", "unique_id"],
                        CrmDataType.ACCOUNT: ["account_id"],
                        CrmDataType.CONTACT: ["contact_id"],
                        # TODO: Add more other CRM types
                        # CrmDataType.ORDER: ["order_id"],
                        # CrmDataType.CONTRACT: ["contract_id"],
                        # CrmDataType.PAYMENTPLAN: ["payment_plan_id"]
                    }
                    
                    # Check the ID field of the corresponding type
                    if crm_type in id_field_map:
                        # user has access if ANY ID is authorized
                        is_authorized = False
                        for id_field in id_field_map[crm_type]:
                            if id_field in meta and meta[id_field]:
                                if crm_authority.is_authorized(crm_type, meta[id_field]):
                                    is_authorized = True 
                                    logger.debug(f"Authorized chunk with {crm_type.value} ID: {meta[id_field]}")
                                    break
                    
                # Check the document metadata if it is still authorized
                if is_authorized and "document_id" in meta:
                    doc_id = meta["document_id"]
                    document = document_repo.get(self.db_session, doc_id)
                    
                    if document and document.meta:
                        doc_meta = document.meta
                        
                        # Check if the document belongs to the CRM category
                        doc_category = doc_meta.get("category")
                        if is_crm_category(doc_category):
                            # First check account permission - if user has account permission,
                            # they automatically have access to all opportunities under it
                            account_permission = False
                            if "account" in doc_meta and isinstance(doc_meta["account"], dict):
                                account_id = doc_meta["account"].get("account_id") or doc_meta["account"].get("unique_id")
                                if account_id and crm_authority.is_authorized(CrmDataType.ACCOUNT, account_id):
                                    account_permission = True
                                    logger.debug(f"Authorized chunk due to account permission: {account_id}")
                            
                            # If no account permission, check opportunity permission
                            if not account_permission:
                                opportunity_id = doc_meta.get("unique_id") or doc_meta.get("opportunity_id")
                                if opportunity_id and not crm_authority.is_authorized(CrmDataType.OPPORTUNITY, opportunity_id):
                                    is_authorized = False
                                    logger.debug(f"Filtering out chunk from unauthorized document with opportunity ID: {opportunity_id}")
 
            # Retain non-CRM document chunks or document chunks with permission
            if is_authorized:
                filtered_nodes.append(node)
        
        logger.info(f"Applied CRM authority filter on document chunks: {len(filtered_nodes)}/{len(nodes)} chunks kept")
        return filtered_nodes
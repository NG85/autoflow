import logging
from typing import List, Optional, Dict, Tuple
from llama_index.core.vector_stores import(
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters
)
from llama_index.core import QueryBundle
from llama_index.core.callbacks import CallbackManager
from llama_index.core.llms import LLM
from llama_index.core.schema import NodeWithScore
from sqlmodel import Session
from app.rag.retrievers.chunk.simple_retriever import (
    ChunkSimpleRetriever,
)
from app.rag.retrievers.chunk.schema import (
    RetrievedChunkDocument,
    VectorSearchRetrieverConfig,
    ChunksRetrievalResult,
    ChunkRetriever,
)
from app.rag.retrievers.chunk.helpers import map_nodes_to_chunks
from app.rag.retrievers.multiple_knowledge_base import MultiKBFusionRetriever
from app.repositories import knowledge_base_repo, document_repo
from app.rag.chat.crm_authority import CRMAuthority
from app.rag.types import CrmDataType

logger = logging.getLogger(__name__)

class ChunkFusionRetriever(MultiKBFusionRetriever, ChunkRetriever):
    def __init__(
        self,
        db_session: Session,
        knowledge_base_ids: List[int],
        llm: LLM,
        use_query_decompose: bool = False,
        config: VectorSearchRetrieverConfig = VectorSearchRetrieverConfig(),
        callback_manager: Optional[CallbackManager] = CallbackManager([]),
        crm_authority: Optional[CRMAuthority] = None,
        granted_files: Optional[List[int]] = None,
        **kwargs,
    ):
        # Prepare vector search retrievers for knowledge bases.
        retrievers = []
        knowledge_bases = knowledge_base_repo.get_by_ids(db_session, knowledge_base_ids)
        self.crm_authority = crm_authority
        self.config = config
        
        query_metadata_filters = None
        if crm_authority and not crm_authority.is_empty():
            # 将CRM权限信息写入filters
            crm_type_filters = []
            unique_id_filters = []
            for crm_type, authorized_ids in crm_authority.authorized_items.items():
                crm_type_filters.append(crm_type.value)
                unique_id_filters.extend(authorized_ids)
            
            # 使用复合条件：category != 'crm' - 非crm类型无需鉴权
            # OR (crm_data_type in [crm_internal_owner, crm_sales_record, crm_stage]) - 这几类crm实体无需鉴权
            # OR (crm_data_type in crm_type_filters AND unique_id in unique_id_filters) - 其他crm实体需要鉴权
            # if not self.config.metadata_filter.filters:
            query_metadata_filters = MetadataFilters(
                filters=[
                    MetadataFilter(key="category", value="crm", operator=FilterOperator.NE),
                    MetadataFilter(key="crm_data_type", value=[CrmDataType.INTERNAL_OWNER.value, CrmDataType.SALES_RECORD.value, CrmDataType.STAGE.value], operator=FilterOperator.IN),
                    MetadataFilters(
                        filters=[
                            MetadataFilter(key="crm_data_type", value=crm_type_filters, operator=FilterOperator.IN),
                            MetadataFilter(key="unique_id", value=unique_id_filters, operator=FilterOperator.IN)
                        ],
                        condition=FilterCondition.AND
                    )
                ],
                condition=FilterCondition.OR
            )
        
        filter_doc_ids = document_repo.fetch_ids_by_file_ids(db_session, granted_files)
        logger.debug(f"Will filter chunks by granted document ids: {len(filter_doc_ids)}")
        
        for kb in knowledge_bases:
            retrievers.append(
                ChunkSimpleRetriever(
                    knowledge_base_id=kb.id,
                    config=self.config,
                    callback_manager=callback_manager,
                    db_session=db_session,
                    filter_doc_ids=filter_doc_ids,
                    query_metadata_filters=query_metadata_filters
                )
            )

        super().__init__(
            db_session=db_session,
            retrievers=retrievers,
            llm=llm,
            use_query_decompose=use_query_decompose,
            callback_manager=callback_manager,
            **kwargs,
        )

    def _fusion(
        self, query: str, results: Dict[Tuple[str, int], List[NodeWithScore]]
    ) -> List[NodeWithScore]:
        return self._simple_fusion(query, results)

    def _simple_fusion(
        self, query: str, results: Dict[Tuple[str, int], List[NodeWithScore]]
    ):
        """Apply simple fusion."""
        # Use a dict to de-duplicate nodes
        all_nodes: Dict[str, NodeWithScore] = {}
        for nodes_with_scores in results.values():
            for node_with_score in nodes_with_scores:
                hash = node_with_score.node.hash
                if hash in all_nodes:
                    max_score = max(
                        node_with_score.score or 0.0, all_nodes[hash].score or 0.0
                    )
                    all_nodes[hash].score = max_score
                else:
                    all_nodes[hash] = node_with_score

        return sorted(all_nodes.values(), key=lambda x: x.score or 0.0, reverse=True)

    def retrieve_chunks(
        self,
        query_str: str,
        full_document: bool = False,
    ) -> ChunksRetrievalResult:
        nodes_with_score = self._retrieve(QueryBundle(query_str))
        chunks = map_nodes_to_chunks(nodes_with_score)

        document_ids = [c.document_id for c in chunks]
        documents = document_repo.fetch_by_ids(self._db_session, document_ids)
        if full_document:
            return ChunksRetrievalResult(chunks=chunks, documents=documents)
        else:
            return ChunksRetrievalResult(
                chunks=chunks,
                documents=[
                    RetrievedChunkDocument(
                        id=d.id, name=d.name, source_uri=d.source_uri
                    )
                    for d in documents
                ],
            )

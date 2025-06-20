import logging

from sqlmodel import Session
from typing import Generator, List, Optional, Dict, Tuple
from llama_index.core.async_utils import run_async_tasks
from llama_index.core import QueryBundle
from llama_index.core.callbacks import CallbackManager
from llama_index.core.schema import NodeWithScore
from llama_index.core.llms import LLM

from app.models import KnowledgeBase
from app.rag.retrievers.multiple_knowledge_base import MultiKBFusionRetriever
from app.rag.retrievers.knowledge_graph.simple_retriever import (
    KnowledgeGraphSimpleRetriever,
)
from app.rag.retrievers.knowledge_graph.schema import (
    KnowledgeGraphRetrieverConfig,
    KnowledgeGraphRetrievalResult,
    KnowledgeGraphNode,
    KnowledgeGraphRetriever,
    MetadataFilterConfig,
)
from app.rag.types import ChatMessageSate, CrmDataType, MyCBEventType
from app.repositories import knowledge_base_repo, document_repo
from app.rag.chat.crm_authority import CRMAuthority


logger = logging.getLogger(__name__)


class KnowledgeGraphFusionRetriever(MultiKBFusionRetriever, KnowledgeGraphRetriever):
    knowledge_base_map: Dict[int, KnowledgeBase] = {}

    def __init__(
        self,
        db_session: Session,
        knowledge_base_ids: List[int],
        llm: LLM,
        use_query_decompose: bool = False,
        config: KnowledgeGraphRetrieverConfig = KnowledgeGraphRetrieverConfig(),
        callback_manager: Optional[CallbackManager] = CallbackManager([]),
        crm_authority: Optional[CRMAuthority] = None,
        granted_files: Optional[List[int]] = None,
        **kwargs,
    ):
        self.use_query_decompose = use_query_decompose

        # Prepare knowledge graph retrievers for knowledge bases.
        retrievers = []
        knowledge_bases = knowledge_base_repo.get_by_ids(db_session, knowledge_base_ids)
        self.knowledge_bases = knowledge_bases
        self.crm_authority = crm_authority
        self.config = config

        if crm_authority and not crm_authority.is_empty():
            # 确保metadata_filter存在并启用
            if not self.config.metadata_filter:
                self.config.metadata_filter = MetadataFilterConfig()
            
            self.config.metadata_filter.enabled = True
            
            # 将CRM权限信息写入filters
            crm_type_filters = []
            unique_id_filters = []
            for crm_type, authorized_ids in crm_authority.authorized_items.items():
                crm_type_filters.append(crm_type)
                unique_id_filters.extend(authorized_ids)
            
            # 使用复合条件：category != 'crm' - 非crm类型无需鉴权
            # OR (crm_data_type in [crm_internal_owner, crm_sales_record, crm_stage]) - 这几类crm实体无需鉴权
            # OR (crm_data_type in crm_type_filters AND unique_id in unique_id_filters) - 其他crm实体需要鉴权
            self.config.metadata_filter.filters = {
                "$or": [
                    {"category": {"$ne": "crm"}},
                    {"crm_data_type": {"$in": [CrmDataType.INTERNAL_OWNER.value, CrmDataType.SALES_RECORD.value, CrmDataType.STAGE.value]}},
                    {
                        "$and": [
                            {"crm_data_type": {"$in": crm_type_filters}},
                            {"unique_id": {"$in": unique_id_filters}}
                        ]
                    }
                ]
            }
        filter_doc_ids = document_repo.fetch_ids_by_file_ids(db_session, granted_files)
        logger.debug(f"Will filter knowledge graph by granted document ids: {filter_doc_ids}")
        
        for kb in knowledge_bases:
            self.knowledge_base_map[kb.id] = kb
            retrievers.append(
                KnowledgeGraphSimpleRetriever(
                    db_session=db_session,
                    knowledge_base_id=kb.id,
                    config=config,
                    callback_manager=callback_manager,
                    filter_doc_ids=filter_doc_ids
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

    def retrieve_knowledge_graph(
        self, query_text: str
    ) -> Generator[Tuple[ChatMessageSate, str], None, KnowledgeGraphRetrievalResult]:
        retrieve_gen = self._retrieve(QueryBundle(query_text))
        
        try:
            # Forward the intermediate state and message of the generator
            nodes_with_score = []
            while True:
                try:
                    state, message = next(retrieve_gen)
                    yield (state, message)
                except StopIteration as e:
                    if hasattr(e, 'value'):
                        nodes_with_score = e.value
                    break
        except Exception as e:
            yield (ChatMessageSate.KG_RETRIEVAL, f"Error during knowledge graph retrieval: {str(e)}")
            return KnowledgeGraphRetrievalResult()
            
        if not nodes_with_score or len(nodes_with_score) == 0:
            return KnowledgeGraphRetrievalResult()
        node: KnowledgeGraphNode = nodes_with_score[0].node  # type:ignore

        return KnowledgeGraphRetrievalResult(
            query=node.query,
            knowledge_bases=[kb.to_descriptor() for kb in self.knowledge_bases],
            entities=node.entities,
            relationships=node.relationships,
            subgraphs=[
                KnowledgeGraphRetrievalResult(
                    query=child_node.query,
                    knowledge_base=self.knowledge_base_map[
                        child_node.knowledge_base_id
                    ].to_descriptor(),
                    entities=child_node.entities,
                    relationships=child_node.relationships,
                )
                for child_node in node.children
            ],
        )

    def _retrieve(self, query_bundle: QueryBundle) -> Generator[Tuple[ChatMessageSate, str], None, List[NodeWithScore]]:
        if self._use_query_decompose:
            queries = self._gen_sub_queries(query_bundle)
        else:
            queries = [query_bundle]

        with self.callback_manager.event(
            MyCBEventType.RUN_SUB_QUERIES, payload={"queries": queries}
        ):
            tasks, task_queries = [], []
            
            yield (ChatMessageSate.KG_RETRIEVAL, "Preparing to execute knowledge graph retrieval")
            for query in queries:
                for i, retriever in enumerate(self._retrievers):
                    tasks.append(retriever.aretrieve(query.query_str))
                    task_queries.append((query.query_str, i))

            yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Executing {len(tasks)} knowledge graph retrievals in parallel")
            task_results = run_async_tasks(tasks)
            results = {}
            total_nodes = 0
            for query_tuple, query_result in zip(task_queries, task_results):
                results[query_tuple] = query_result
                total_nodes += len(query_result)
        
            fused_results = self._fusion(query_bundle.query_str, results)
            # Count entities and relationships in fused results
            entity_count = 0
            relationship_count = 0
            if fused_results and len(fused_results) > 0:
                node = fused_results[0].node
                entity_count = len(node.entities)
                relationship_count = len(node.relationships)
            
            yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Knowledge graph retrieval and fusion completed with {entity_count} entities and {relationship_count} relationships")
            return fused_results


    def _gen_sub_queries(self, query_bundle: QueryBundle) -> List[QueryBundle]:
        queries = self._query_decomposer.decompose(query_bundle.query_str)
        return [QueryBundle(r.question) for r in queries.questions]
            
    def _fusion(
        self, query: str, results: Dict[Tuple[str, int], List[NodeWithScore]]
    ) -> List[NodeWithScore]:
        return self._knowledge_graph_fusion(query, results)

    def _knowledge_graph_fusion(
        self, query: str, results: Dict[Tuple[str, int], List[NodeWithScore]]
    ) -> List[NodeWithScore]:
        merged_entities = set()
        merged_relationships = {}
        merged_knowledge_base_ids = set()
        merged_children_nodes = []

        for nodes_with_scores in results.values():
            if len(nodes_with_scores) == 0:
                continue
            node: KnowledgeGraphNode = nodes_with_scores[0].node  # type:ignore

            # Merge knowledge base id.
            merged_knowledge_base_ids.add(node.knowledge_base_id)

            # Merge entities.
            merged_entities.update(node.entities)

            # Merge relationships.
            for r in node.relationships:
                key = r.rag_description
                if key not in merged_relationships:
                    merged_relationships[key] = r
                else:
                    merged_relationships[key].weight += r.weight
            # Merge to children nodes.
            merged_children_nodes.append(node)

        return [
            NodeWithScore(
                node=KnowledgeGraphNode(
                    query=query,
                    entities=list(merged_entities),
                    relationships=list(merged_relationships.values()),
                    knowledge_base_ids=merged_knowledge_base_ids,
                    children=merged_children_nodes,
                ),
                score=1,
            )
        ]

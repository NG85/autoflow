import logging

from sqlmodel import Session
from typing import Generator, List, Optional, Dict, Tuple
from llama_index.core.async_utils import run_async_tasks
from llama_index.core import QueryBundle
from llama_index.core.callbacks import CallbackManager
from llama_index.core.llms import LLM
from llama_index.core.schema import NodeWithScore
from llama_index.core.tools import ToolMetadata

from app.models import KnowledgeBase
from app.rag.retrievers.multiple_knowledge_base import MultiKBFusionRetriever
from app.rag.knowledge_base.selector import KBSelectMode
from app.rag.retrievers.knowledge_graph.simple_retriever import (
    KnowledgeGraphSimpleRetriever,
)
from app.rag.retrievers.knowledge_graph.schema import (
    KnowledgeGraphRetrieverConfig,
    KnowledgeGraphRetrievalResult,
    KnowledgeGraphNode,
    KnowledgeGraphRetriever,
)
from app.rag.types import ChatMessageSate, MyCBEventType
from app.repositories import knowledge_base_repo


logger = logging.getLogger(__name__)


class KnowledgeGraphFusionRetriever(MultiKBFusionRetriever, KnowledgeGraphRetriever):
    knowledge_base_map: Dict[int, KnowledgeBase] = {}

    def __init__(
        self,
        db_session: Session,
        knowledge_base_ids: List[int],
        llm: LLM,
        use_query_decompose: bool = False,
        kb_select_mode: KBSelectMode = KBSelectMode.ALL,
        use_async: bool = True,
        config: KnowledgeGraphRetrieverConfig = KnowledgeGraphRetrieverConfig(),
        callback_manager: Optional[CallbackManager] = CallbackManager([]),
        **kwargs,
    ):
        self.use_query_decompose = use_query_decompose

        # Prepare knowledge graph retrievers for knowledge bases.
        retrievers = []
        retriever_choices = []
        knowledge_bases = knowledge_base_repo.get_by_ids(db_session, knowledge_base_ids)
        self.knowledge_bases = knowledge_bases
        for kb in knowledge_bases:
            self.knowledge_base_map[kb.id] = kb
            retrievers.append(
                KnowledgeGraphSimpleRetriever(
                    db_session=db_session,
                    knowledge_base_id=kb.id,
                    config=config,
                    callback_manager=callback_manager,
                )
            )
            retriever_choices.append(
                ToolMetadata(
                    name=kb.name,
                    description=kb.description,
                )
            )

        super().__init__(
            db_session=db_session,
            retrievers=retrievers,
            retriever_choices=retriever_choices,
            llm=llm,
            use_query_decompose=use_query_decompose,
            kb_select_mode=kb_select_mode,
            use_async=use_async,
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
            results = {}
            if self._use_async:
                try:
                    results_async_gen = self._run_async_queries(queries)
                    try:
                        while True:
                            try:
                                state, message = next(results_async_gen)
                                yield (state, message)
                            except StopIteration as e:
                                if hasattr(e, 'value'):
                                    results = e.value
                                break
                            except Exception as e:
                                yield (ChatMessageSate.KG_RETRIEVAL, f"Error during async query execution: {str(e)}")
                                results = {}
                    except Exception as e:
                        yield (ChatMessageSate.KG_RETRIEVAL, f"Error setting up async queries: {str(e)}")
                        results = {}
                except Exception as e:
                    yield (ChatMessageSate.KG_RETRIEVAL, f"Error setting up async queries: {str(e)}")
                    results = {}
            else:
                try:
                    results_sync_gen = self._run_sync_queries(queries)
                    try:
                        while True:
                            try:
                                state, message = next(results_sync_gen)
                                yield (state, message)
                            except StopIteration as e:
                                if hasattr(e, 'value'):
                                    results = e.value
                                break
                    except Exception as e:
                        yield (ChatMessageSate.KG_RETRIEVAL, f"Error during sync query execution: {str(e)}")
                        results = {}
                except Exception as e:
                    yield (ChatMessageSate.KG_RETRIEVAL, f"Error setting up sync queries: {str(e)}")
                    results = {}
                    
        if not results:
            yield (ChatMessageSate.KG_RETRIEVAL, "No results retrieved from knowledge sources")
            return []
        try:
            fused_results = self._fusion(query_bundle.query_str, results)
            yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Result fusion completed and found {len(fused_results)} related nodes")
            return fused_results
        except Exception as e:
            yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Error during result fusion: {str(e)}")
            return []


    def _gen_sub_queries(self, query_bundle: QueryBundle) -> List[QueryBundle]:
        queries = self._query_decomposer.decompose(query_bundle.query_str)
        return [QueryBundle(r.question) for r in queries.questions]

    def _run_async_queries(
        self, queries: List[QueryBundle]
    ) -> Generator[Tuple[ChatMessageSate, str], None, Dict[Tuple[str, int], List[NodeWithScore]]]:
        tasks, task_queries = [], []

        sections_by_query = {}
        total_sections = 0
        for query in queries:
            sections = self._selector.select(query)
            sections_by_query[query.query_str] = sections
            total_sections += len(sections)

        if total_sections == 0:
            yield (ChatMessageSate.KG_RETRIEVAL, "No suitable knowledge sources found")
            return {}
        yield (ChatMessageSate.KG_RETRIEVAL, "Preparing to execute knowledge graph retrieval")
      
        for query in queries:
            sections = sections_by_query[query.query_str]
            for retriever, i in sections:
                tasks.append(retriever.aretrieve(query.query_str))
                task_queries.append((query.query_str, i))

        yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Executing {len(tasks)} knowledge graph retrievals in parallel")
        task_results = run_async_tasks(tasks)
        results = {}
        total_nodes = 0
        for query_tuple, query_result in zip(task_queries, task_results):
            results[query_tuple] = query_result
            total_nodes += len(query_result)
            
        yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Retrieval completed and found {total_nodes} related nodes")

        return results

    def _run_sync_queries(
        self, queries: List[QueryBundle]
    ) -> Generator[Tuple[ChatMessageSate, str], None, Dict[Tuple[str, int], List[NodeWithScore]]]:
        yield (ChatMessageSate.KG_RETRIEVAL, "Start selecting the appropriate knowledge base")
        sections_by_query = {}
        total_sections = 0
        results = {}
        for query in queries:
            sections = self._selector.select(query)
            sections_by_query[query.query_str] = sections
            total_sections += len(sections)

        if total_sections == 0:
            yield (ChatMessageSate.KG_RETRIEVAL, "No suitable knowledge sources found")
            return {}
        yield (ChatMessageSate.KG_RETRIEVAL, "Preparing to execute knowledge graph retrieval")

        results = {}
        completed = 0
        for query in queries:
            sections = sections_by_query[query.query_str]
            for retriever, i in sections:
                kb_name = getattr(retriever, 'knowledge_base', None)
                kb_name = kb_name.name if kb_name else f"Knowledge base #{i}"
                yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Searching: {kb_name}")
                results[(query.query_str, i)] = retriever.retrieve(query)
                
                completed += 1
                progress = int(completed / total_sections * 100)
                yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Searching progress: {progress}%")

        total_nodes = sum(len(nodes) for nodes in results.values())
        yield (ChatMessageSate.KG_QUERY_EXECUTION, f"Retrieval completed and found {total_nodes} related nodes")
        
        return results
            
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

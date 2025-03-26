import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination import Params, Page
from app.api.deps import SessionDep, CurrentSuperuserDep
from app.rag.knowledge_base.index_store import (
    init_kb_tidb_vector_store,
    init_kb_tidb_graph_store,
)
from app.repositories.embedding_model import embed_model_repo
from app.repositories.llm import llm_repo
from app.models.enums import GraphType

from .models import (
    KnowledgeBaseDetail,
    KnowledgeBaseItem,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    RetryFailedTasksRequest,
    VectorIndexError,
    KGIndexError,
)
from app.exceptions import (
    InternalServerError,
    KBIsUsedByChatEngines,
)
from app.models import (
    DataSource,
    KnowledgeBase,
)
from app.repositories import (
    embed_model_repo,
    llm_repo,
    data_source_repo,
    knowledge_base_repo,
)
from app.tasks import (
    build_kg_index_for_chunk,
    build_index_for_document,
    build_vector_index_for_chunk,
    build_vector_index_for_entity,
    build_vector_index_for_relationship,
    build_playbook_kg_index_for_chunk,
    build_crm_graph_index_for_document,
)
from app.tasks.knowledge_base import (
    import_documents_for_knowledge_base,
    stats_for_knowledge_base,
    purge_knowledge_base_related_resources,
)
from ..models import ChatEngineDescriptor

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/admin/knowledge_bases")
def create_knowledge_base(
    session: SessionDep, user: CurrentSuperuserDep, create: KnowledgeBaseCreate
) -> KnowledgeBaseDetail:
    try:
        data_sources = [
            data_source_repo.create(
                session,
                DataSource(
                    name=data_source.name,
                    description="",
                    user_id=user.id,
                    data_source_type=data_source.data_source_type,
                    config=data_source.config,
                ),
            )
            for data_source in create.data_sources
        ]

        if not create.llm_id:
            create.llm_id = llm_repo.must_get_default(session).id

        if not create.embedding_model_id:
            create.embedding_model_id = embed_model_repo.must_get_default(session).id

        knowledge_base = KnowledgeBase(
            name=create.name,
            description=create.description,
            index_methods=create.index_methods,
            llm_id=create.llm_id,
            embedding_model_id=create.embedding_model_id,
            chunking_config=create.chunking_config.model_dump(),
            data_sources=data_sources,
            created_by=user.id,
            updated_by=user.id,
        )
        knowledge_base = knowledge_base_repo.create(session, knowledge_base)

        # Ensure the knowledge-base corresponding table schema are initialized.
        init_kb_tidb_vector_store(session, knowledge_base)
        init_kb_tidb_graph_store(session, knowledge_base)

        # Trigger import and index documents for knowledge base
        import_documents_for_knowledge_base.delay(knowledge_base.id)

        return knowledge_base
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/admin/knowledge_bases")
def list_knowledge_bases(
    session: SessionDep,
    user: CurrentSuperuserDep,
    params: Params = Depends(),
) -> Page[KnowledgeBaseItem]:
    return knowledge_base_repo.paginate(session, params)


@router.get("/admin/knowledge_bases/{knowledge_base_id}")
def get_knowledge_base(
    session: SessionDep,
    user: CurrentSuperuserDep,
    knowledge_base_id: int,
) -> KnowledgeBaseDetail:
    try:
        return knowledge_base_repo.must_get(session, knowledge_base_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.put("/admin/knowledge_bases/{knowledge_base_id}")
def update_knowledge_base_setting(
    session: SessionDep,
    user: CurrentSuperuserDep,
    knowledge_base_id: int,
    update: KnowledgeBaseUpdate,
) -> KnowledgeBaseDetail:
    try:
        knowledge_base = knowledge_base_repo.must_get(session, knowledge_base_id)
        knowledge_base = knowledge_base_repo.update(session, knowledge_base, update)
        return knowledge_base
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/admin/knowledge_bases/{kb_id}/linked_chat_engines")
def list_kb_linked_chat_engines(
    session: SessionDep, user: CurrentSuperuserDep, kb_id: int
) -> list[ChatEngineDescriptor]:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        return knowledge_base_repo.list_linked_chat_engines(session, kb.id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.delete("/admin/knowledge_bases/{kb_id}")
def delete_knowledge_base(session: SessionDep, user: CurrentSuperuserDep, kb_id: int):
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)

        # Check if the knowledge base has linked chat engines.
        linked_chat_engines = knowledge_base_repo.list_linked_chat_engines(
            session, kb.id
        )
        if len(linked_chat_engines) > 0:
            raise KBIsUsedByChatEngines(kb_id, len(linked_chat_engines))

        # Delete knowledge base.
        knowledge_base_repo.delete(session, kb)

        # Trigger purge knowledge base related resources after 5 seconds.
        purge_knowledge_base_related_resources.apply_async(args=[kb_id], countdown=5)

        return {"detail": f"Knowledge base #{kb_id} is deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/admin/knowledge_bases/{knowledge_base_id}/overview")
def get_knowledge_base_index_overview(
    session: SessionDep,
    user: CurrentSuperuserDep,
    knowledge_base_id: int,
) -> dict:
    try:
        knowledge_base = knowledge_base_repo.must_get(session, knowledge_base_id)

        stats_for_knowledge_base.delay(knowledge_base.id)

        return knowledge_base_repo.get_index_overview(session, knowledge_base)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/admin/knowledge_bases/{kb_id}/vector-index-errors")
def list_kb_vector_index_errors(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
    params: Params = Depends(),
) -> Page[VectorIndexError]:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        return knowledge_base_repo.list_vector_index_built_errors(session, kb, params)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.get("/admin/knowledge_bases/{kb_id}/kg-index-errors")
def list_kb_kg_index_errors(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
    params: Params = Depends(),
) -> Page[KGIndexError]:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        return knowledge_base_repo.list_kg_index_built_errors(session, kb, params)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/admin/knowledge_bases/{kb_id}/retry-failed-index-tasks")
def retry_failed_tasks(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
    request: RetryFailedTasksRequest = None, 
) -> dict:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        
        graph_types = request.graph_types if request else [
            GraphType.general
        ]
        
        document_ids = []
        chunk_ids = []
        playbook_chunk_ids = []
        crm_document_ids = []
        
        if GraphType.general in graph_types:
            # Retry failed vector index tasks.
            document_ids = knowledge_base_repo.set_failed_documents_status_to_pending(
                session, kb
            )
            for document_id in document_ids:
                build_index_for_document.delay(kb_id, document_id)
            logger.info(f"Triggered {len(document_ids)} documents to rebuild vector index.")

            # Retry failed kg index tasks.
            chunk_ids = knowledge_base_repo.set_failed_chunks_status_to_pending(session, kb)
            for chunk_id in chunk_ids:
                build_kg_index_for_chunk.delay(kb_id, chunk_id)
            logger.info(f"Triggered {len(chunk_ids)} chunks to rebuild knowledge graph index.")

        if GraphType.playbook in graph_types:
            # Retry failed playbook kg index tasks
            playbook_chunk_ids = knowledge_base_repo.set_failed_playbook_chunks_status_to_pending(session, kb)
            for chunk_id in playbook_chunk_ids:
                build_playbook_kg_index_for_chunk.delay(kb_id, chunk_id)
            logger.info(f"Triggered {len(playbook_chunk_ids)} chunks to rebuild playbook knowledge graph index.")

        if GraphType.crm in graph_types:
            # Retry failed crm kg index tasks
            crm_document_ids = knowledge_base_repo.set_failed_crm_documents_chunks_status_to_pending(session, kb)
            for document_id in crm_document_ids:
                build_crm_graph_index_for_document.delay(kb_id, document_id)
            logger.info(f"Triggered {len(crm_document_ids)} crm documents to rebuild crm knowledge graph index.")

        return {
            "detail": f"Triggered reindex {len(document_ids)} documents, {len(chunk_ids)} chunks, {len(playbook_chunk_ids)} playbook chunks and {len(crm_document_ids)} crm documents of knowledge base #{kb_id}.",
            "reindex_document_ids": document_ids,
            "reindex_chunk_ids": chunk_ids,
            "reindex_playbook_chunk_ids": playbook_chunk_ids,
            "reindex_crm_document_ids": crm_document_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()


@router.post("/admin/knowledge_bases/{kb_id}/build-playbook-graph-index")
def build_playbook_failed_chunks_graph_index(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
) -> dict:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        chunk_ids = knowledge_base_repo.prepare_chunks_to_build_playbook_index(session, kb)
        
        for chunk_id in chunk_ids:
            build_playbook_kg_index_for_chunk.delay(kb_id, chunk_id)
        logger.info(f"Triggered {len(chunk_ids)} chunks to build playbook knowledge graph index.")

        return {
            "detail": f"Triggered index {len(chunk_ids)} chunks of playbook knowledge base #{kb_id}.",
            "index_playbook_chunk_ids": chunk_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()

@router.post("/admin/knowledge_bases/{kb_id}/build-crm-graph-index")
def build_crm_failed_documents_graph_index(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
) -> dict:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        document_ids = knowledge_base_repo.prepare_documents_to_build_crm_index(session, kb)

        for document_id in document_ids:
            build_crm_graph_index_for_document.delay(kb_id, document_id)
        logger.info(f"Triggered {len(document_ids)} crm documents to build crm knowledge graph index.")
        return {
            "detail": f"Triggered index {len(document_ids)} crm documents of crm knowledge base #{kb_id}.",
            "index_crm_document_ids": document_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
    

@router.post("/admin/knowledge_bases/{kb_id}/build-entity-vectors")
def build_entity_vectors(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
) -> dict:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)            
        entity_ids = knowledge_base_repo.get_entities_to_build_vector_index(session, kb)
                
        for entity_id in entity_ids:
            build_vector_index_for_entity.delay(kb_id, entity_id)
        
        logger.info(f"Triggered {len(entity_ids)} entities to build vector embeddings.")

        return {
            "detail": f"Triggered vector embedding generation for {len(entity_ids)} entities of knowledge base #{kb_id}.",
            "vector_index_entity_ids": entity_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
 
    
@router.post("/admin/knowledge_bases/{kb_id}/build-relationship-vectors")
def build_relationship_vectors(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
) -> dict:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)            
        relation_ids = knowledge_base_repo.get_relationships_to_build_vector_index(session, kb)
                
        for relation_id in relation_ids:
            build_vector_index_for_relationship.delay(kb_id, relation_id)
        
        logger.info(f"Triggered {len(relation_ids)} relations to build vector embeddings.")

        return {
            "detail": f"Triggered vector embedding generation for {len(relation_ids)} relations of knowledge base #{kb_id}.",
            "vector_index_relation_ids": relation_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
 
    
@router.post("/admin/knowledge_bases/{kb_id}/build-chunk-vectors")
def build_chunk_vectors(
    session: SessionDep,
    user: CurrentSuperuserDep,
    kb_id: int,
) -> dict:
    try:
        kb = knowledge_base_repo.must_get(session, kb_id)
        # Retry failed vector index tasks.
        chunk_ids = knowledge_base_repo.get_chunks_to_build_vector_index(
            session, kb
        )
        for chunk_id in chunk_ids:
            build_vector_index_for_chunk.delay(kb_id, chunk_id)
        logger.info(f"Triggered {len(chunk_ids)} chunks to build vector embeddings.")

        return {
            "detail": f"Triggered vector embedding generation for {len(chunk_ids)} chunks of knowledge base #{kb_id}.",
            "vector_index_chunk_ids": chunk_ids,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(e)
        raise InternalServerError()
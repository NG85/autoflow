from celery.utils.log import get_task_logger
from sqlalchemy import delete, func, select
from sqlalchemy.orm import load_only
from sqlmodel import Session

from app.celery import app as celery_app
from app.core.db import engine
from app.exceptions import KBNotFound
from app.models import (
    Document,
    KnowledgeBaseDataSource,
    DataSource,
    Upload,
)
from app.rag.datasource import get_data_source_loader
from app.models.document import DocIndexTaskStatus
from app.models.document import DocumentCategory
from app.repositories import knowledge_base_repo, document_repo
from .build_index import build_index_for_document
from ..models.chunk import get_kb_chunk_model
from ..models.entity import get_kb_entity_model
from ..models.relationship import get_kb_relationship_model
from ..rag.knowledge_base.index_store import (
    get_kb_tidb_vector_store,
    get_kb_tidb_graph_store,
)
from ..repositories.chunk import ChunkRepo
from ..repositories.graph import GraphRepo

logger = get_task_logger(__name__)


def _normalize_crm_data_type(value) -> str | None:
    if not value:
        return None
    # CrmDataType is a str Enum; str(member) is "CrmDataType.X", not enum value.
    return str(getattr(value, "value", value))


@celery_app.task
def import_documents_for_knowledge_base(kb_id: int):
    try:
        with Session(engine) as session:
            kb = knowledge_base_repo.must_get(session, kb_id)
            data_sources = kb.data_sources
            for data_source in data_sources:
                import_documents_from_kb_datasource(kb.id, data_source.id)

        logger.info(f"Successfully imported documents for knowledge base #{kb_id}")
    except KBNotFound:
        logger.error(f"Knowledge base #{kb_id} is not found")
    except Exception as e:
        logger.exception(
            f"Failed to import documents for knowledge base #{kb_id}", exc_info=e
        )


@celery_app.task
def import_documents_from_kb_datasource(kb_id: int, data_source_id: int):
    try:
        with Session(engine) as session:
            kb = knowledge_base_repo.must_get(session, kb_id)
            data_source = knowledge_base_repo.must_get_kb_datasource(
                session, kb, data_source_id
            )
            chunk_model = get_kb_chunk_model(kb)
            chunk_repo = ChunkRepo(chunk_model)
            graph_repo = GraphRepo(
                get_kb_entity_model(kb),
                get_kb_relationship_model(kb),
                chunk_model,
            )

            logger.info(
                f"Loading documents from data source #{data_source_id} for knowledge base #{kb_id}"
            )
            loader = get_data_source_loader(
                session,
                kb_id,
                data_source.data_source_type,
                data_source.id,
                data_source.user_id,
                data_source.config,
            )

            # Build an in-memory key index for CRM docs to support upsert by business key.
            # Key: (crm_data_type, unique_id)
            existing_crm_docs = {}
            existing_docs = session.scalars(
                select(Document)
                .options(load_only(Document.id, Document.file_id, Document.meta))
                .where(
                    Document.knowledge_base_id == kb_id,
                    func.json_unquote(
                        func.json_extract(Document.meta, "$.category")
                    )
                    == DocumentCategory.CRM.value,
                )
            ).all()
            for db_doc in existing_docs:
                meta = db_doc.meta if isinstance(db_doc.meta, dict) else {}
                crm_data_type = _normalize_crm_data_type(meta.get("crm_data_type"))
                unique_id = meta.get("unique_id")
                if crm_data_type and unique_id:
                    existing_crm_docs[(str(crm_data_type), str(unique_id))] = db_doc

            for document in loader.load_documents():
                meta = document.meta or {}
                is_crm_doc = meta.get("category") == DocumentCategory.CRM
                crm_data_type = _normalize_crm_data_type(meta.get("crm_data_type"))
                unique_id = meta.get("unique_id")
                upsert_key = (
                    (str(crm_data_type), str(unique_id))
                    if is_crm_doc and crm_data_type and unique_id
                    else None
                )

                if upsert_key and upsert_key in existing_crm_docs:
                    existing_doc = existing_crm_docs[upsert_key]
                    old_file_id = existing_doc.file_id
                    logger.info(
                        "Upsert CRM document for key=%s (existing_id=%s, datasource=%s)",
                        upsert_key,
                        existing_doc.id,
                        data_source_id,
                    )
                    # Remove old graph/chunks so re-index can rebuild from the latest content.
                    graph_repo.delete_document_relationships(session, existing_doc.id)
                    chunk_repo.delete_by_document(session, existing_doc.id)
                    graph_repo.delete_orphaned_entities(session)

                    existing_doc.name = document.name
                    existing_doc.hash = document.hash
                    existing_doc.content = document.content
                    existing_doc.mime_type = document.mime_type
                    existing_doc.data_source_id = document.data_source_id
                    existing_doc.file_id = document.file_id
                    existing_doc.source_uri = document.source_uri
                    existing_doc.updated_at = document.updated_at
                    existing_doc.last_modified_at = document.last_modified_at
                    existing_doc.meta = document.meta
                    existing_doc.index_status = DocIndexTaskStatus.NOT_STARTED
                    existing_doc.index_result = None
                    session.add(existing_doc)

                    # Clean up old upload row if no other document references it.
                    if old_file_id and old_file_id != existing_doc.file_id:
                        file_ref_exists = session.exec(
                            select(Document.id).where(
                                Document.file_id == old_file_id,
                                Document.id != existing_doc.id,
                            )
                        ).first()
                        if not file_ref_exists:
                            old_upload = session.get(Upload, old_file_id)
                            if old_upload:
                                session.delete(old_upload)

                    session.commit()
                    build_index_for_document.delay(kb_id, existing_doc.id)
                    continue

                session.add(document)
                session.commit()
                if upsert_key:
                    existing_crm_docs[upsert_key] = document
                build_index_for_document.delay(kb_id, document.id)
                    
        stats_for_knowledge_base.delay(kb_id)
        logger.info(
            f"Successfully imported documents for from datasource #{data_source_id}"
        )
    except Exception as e:
        logger.exception(
            f"Failed to import documents from data source #{data_source_id} of knowledge base #{kb_id}",
            exc_info=e,
        )


@celery_app.task
def stats_for_knowledge_base(kb_id: int):
    try:
        with Session(engine) as session:
            kb = knowledge_base_repo.must_get(session, kb_id)

            documents_total = knowledge_base_repo.count_documents(session, kb)
            data_sources_total = knowledge_base_repo.count_data_sources(session, kb)

            kb.documents_total = documents_total
            kb.data_sources_total = data_sources_total

            session.add(kb)
            session.commit()

        logger.info(f"Successfully running stats for knowledge base #{kb_id}")
    except KBNotFound:
        logger.error(f"Knowledge base #{kb_id} is not found")
    except Exception as e:
        logger.exception(f"Failed to run stats for knowledge base #{kb_id}", exc_info=e)


@celery_app.task
def purge_knowledge_base_related_resources(kb_id: int):
    """
    Purge all resources related to a knowledge base.

    Related resources:
        - documents
        - chunks
        - indexes
            - vector index
            - knowledge graph index
        - data sources
    """

    with Session(engine) as session:
        knowledge_base = knowledge_base_repo.must_get(
            session, kb_id, show_soft_deleted=True
        )
        assert knowledge_base.deleted_at is not None

        data_source_ids = [datasource.id for datasource in knowledge_base.data_sources]

        # Drop entities_{kb_id}, relationships_{kb_id} tables.
        tidb_graph_store = get_kb_tidb_graph_store(session, knowledge_base)
        tidb_graph_store.drop_table_schema()
        logger.info(
            f"Dropped tidb graph store of knowledge base #{kb_id} successfully."
        )

        # Drop chunks_{kb_id} table.
        tidb_vector_store = get_kb_tidb_vector_store(session, knowledge_base)
        tidb_vector_store.drop_table_schema()

        logger.info(
            f"Dropped tidb vector store of knowledge base #{kb_id} successfully."
        )
        
        # Get document file IDs before deleting documents
        documents_with_files = session.exec(
            select(Document.file_id).where(
                Document.knowledge_base_id == kb_id,
                Document.file_id.is_not(None)
            )
        ).all()
        file_ids = [file_id[0] for file_id in documents_with_files if file_id[0]]
        
        # Delete documents.
        stmt = delete(Document).where(Document.knowledge_base_id == kb_id)
        session.exec(stmt)
        logger.info(f"Deleted documents of knowledge base #{kb_id} successfully.")
        
        # Delete associated upload records
        if file_ids:
            stmt = delete(Upload).where(Upload.id.in_(file_ids))
            session.exec(stmt)
            logger.info(f"Deleted {len(file_ids)} upload records associated with knowledge base #{kb_id} successfully.")

        # Delete data sources and links.
        if len(data_source_ids) > 0:
            stmt = delete(KnowledgeBaseDataSource).where(
                KnowledgeBaseDataSource.knowledge_base_id == kb_id
            )
            session.exec(stmt)
            logger.info(
                f"Deleted linked data sources of knowledge base #{kb_id} successfully."
            )

            stmt = delete(DataSource).where(DataSource.id.in_(data_source_ids))
            session.exec(stmt)
            logger.info(
                f"Deleted data sources {', '.join([f'#{did}' for did in data_source_ids])} successfully."
            )

        # Delete knowledge base.
        session.delete(knowledge_base)
        logger.info(f"Deleted knowledge base #{kb_id} successfully.")

        session.commit()


@celery_app.task
def purge_kb_datasource_related_resources(kb_id: int, datasource_id: int):
    """
    Purge all resources related to the deleted datasource in the knowledge base.
    """

    with Session(engine) as session:
        kb = knowledge_base_repo.must_get(session, kb_id, show_soft_deleted=True)
        datasource = knowledge_base_repo.must_get_kb_datasource(
            session, kb, datasource_id, show_soft_deleted=True
        )
        assert datasource.deleted_at is not None

        chunk_model = get_kb_chunk_model(kb)
        entity_model = get_kb_entity_model(kb)
        relationship_model = get_kb_relationship_model(kb)

        chunk_repo = ChunkRepo(chunk_model)
        graph_repo = GraphRepo(entity_model, relationship_model, chunk_model)

        graph_repo.delete_data_source_relationships(session, datasource_id)
        logger.info(
            f"Deleted relationships generated by chunks from data source #{datasource_id} successfully."
        )

        graph_repo.delete_orphaned_entities(session)
        logger.info("Deleted orphaned entities successfully.")

        chunk_repo.delete_by_datasource(session, datasource_id)
        logger.info(f"Deleted chunks from data source #{datasource_id} successfully.")

        # Get document IDs with file_id before deleting them
        documents_with_files = session.exec(
            select(Document.file_id).where(
                Document.data_source_id == datasource_id,
                Document.file_id.is_not(None)
            )
        ).all()
        
        # Delete documents first
        document_repo.delete_by_datasource(session, datasource_id)
        logger.info(
            f"Deleted documents from data source #{datasource_id} successfully."
        )
        
        # Delete associated uploads
        if documents_with_files:
            file_ids = [file_id[0] for file_id in documents_with_files if file_id[0]]
            if file_ids:
                session.exec(delete(Upload).where(Upload.id.in_(file_ids)))
                logger.info(
                    f"Deleted {len(file_ids)} uploads associated with data source #{datasource_id} successfully."
                )

        session.delete(datasource)
        logger.info(f"Deleted data source #{datasource_id} successfully.")

        session.commit()

    stats_for_knowledge_base.delay(kb_id)

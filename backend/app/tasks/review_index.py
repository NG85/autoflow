"""Celery task that orchestrates full indexing for a review session.

Usage:
    index_review_session_data.delay(session_id="<review-session-unique-id>")
    index_review_session_data.delay(session_id="...", kb_id=3)
"""

import traceback
from typing import List, Optional

from celery import chain
from celery.utils.log import get_task_logger
from sqlmodel import Session, select

from app.celery import app as celery_app
from app.core.config import settings
from app.core.db import engine
from app.models import Document as DBDocument, Upload
from app.models.document import DocIndexTaskStatus, DocumentCategory
from app.models.chunk import get_kb_chunk_model
from app.models.entity import get_kb_entity_model
from app.models.relationship import get_kb_relationship_model
from app.rag.datasource.review import ReviewDataSource
from app.rag.types import CrmDataType
from app.rag.chat.review.indexing_policy import (
    get_or_create_review_datasource_id,
    normalize_review_data_types,
    validate_review_index_scope_by_stage,
)
from app.repositories import knowledge_base_repo
from app.repositories.crm_review_session import crm_review_session_repo
from app.repositories.chunk import ChunkRepo
from app.repositories.graph import GraphRepo
from app.tasks.build_crm_index import build_crm_graph_index_for_document
from app.tasks.build_index import build_index_for_document

logger = get_task_logger(__name__)


def _normalize_crm_data_type(value) -> str | None:
    if not value:
        return None
    return str(getattr(value, "value", value))


@celery_app.task(bind=True)
def index_review_session_data(
    self,
    session_id: str,
    kb_id: int = None,
    review_data_types: Optional[List[str]] = None,
):
    """Index all review data for a given session into vector store and KG.

    Parameters
    ----------
    session_id : str
        The ``unique_id`` of the review session to index.
    kb_id : int | None
        Knowledge base to store documents in.  Falls back to
        ``settings.CRM_REVIEW_KB_ID`` when not provided.
    """
    kb_id = kb_id or settings.CRM_REVIEW_KB_ID
    selected_types = normalize_review_data_types(review_data_types)
    logger.info(
        "Starting review session indexing for session=%s, kb=%s, types=%s",
        session_id,
        kb_id,
        selected_types,
    )

    try:
        with Session(engine, expire_on_commit=False) as db_session:
            session_obj = crm_review_session_repo.get_by_unique_id(db_session, session_id)
            if not session_obj:
                raise ValueError(f"Review session not found: {session_id}")
            validate_review_index_scope_by_stage(session_obj.stage, selected_types)

            kb = knowledge_base_repo.must_get(db_session, kb_id)

            data_source_id = get_or_create_review_datasource_id(
                db_session,
                kb,
                session_id=session_id,
                session_name=session_obj.session_name,
            )

            loader = ReviewDataSource(
                db_session=db_session,
                knowledge_base_id=kb.id,
                data_source_id=data_source_id,
                user_id=None,
                review_session_id=session_id,
                include_data_types=[CrmDataType(v) for v in selected_types],
            )
            chunk_model = get_kb_chunk_model(kb)
            chunk_repo = ChunkRepo(chunk_model)
            graph_repo = GraphRepo(
                get_kb_entity_model(kb),
                get_kb_relationship_model(kb),
                chunk_model,
            )
            existing_crm_docs = {}
            existing_docs = db_session.exec(
                select(DBDocument).where(DBDocument.knowledge_base_id == kb.id)
            ).all()
            for db_doc in existing_docs:
                meta = db_doc.meta or {}
                if meta.get("category") != DocumentCategory.CRM:
                    continue
                crm_data_type = _normalize_crm_data_type(meta.get("crm_data_type"))
                unique_id = meta.get("unique_id")
                if crm_data_type and unique_id:
                    existing_crm_docs[(str(crm_data_type), str(unique_id))] = db_doc

            doc_count = 0
            inserted_count = 0
            upserted_count = 0
            cleaned_upload_count = 0
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
                        "Upsert review CRM document for key=%s (existing_id=%s, datasource=%s)",
                        upsert_key,
                        existing_doc.id,
                        data_source_id,
                    )
                    graph_repo.delete_document_relationships(db_session, existing_doc.id)
                    chunk_repo.delete_by_document(db_session, existing_doc.id)
                    graph_repo.delete_orphaned_entities(db_session)
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
                    db_session.add(existing_doc)
                    if old_file_id and old_file_id != existing_doc.file_id:
                        file_ref_exists = db_session.exec(
                            select(DBDocument.id).where(
                                DBDocument.file_id == old_file_id,
                                DBDocument.id != existing_doc.id,
                            )
                        ).first()
                        if not file_ref_exists:
                            old_upload = db_session.get(Upload, old_file_id)
                            if old_upload:
                                db_session.delete(old_upload)
                                cleaned_upload_count += 1
                    db_session.commit()
                    chain(
                        build_index_for_document.si(kb.id, existing_doc.id),
                        build_crm_graph_index_for_document.si(kb.id, existing_doc.id),
                    ).delay()
                    doc_count += 1
                    upserted_count += 1
                    continue

                db_session.add(document)
                db_session.commit()
                db_session.refresh(document)
                if upsert_key:
                    existing_crm_docs[upsert_key] = document

                chain(
                    build_index_for_document.si(kb.id, document.id),
                    build_crm_graph_index_for_document.si(kb.id, document.id),
                ).delay()
                doc_count += 1
                inserted_count += 1

        logger.info(
            "Review session indexing complete for session=%s: queued=%s, inserted=%s, upserted=%s, cleaned_uploads=%s",
            session_id,
            doc_count,
            inserted_count,
            upserted_count,
            cleaned_upload_count,
        )
    except ValueError as e:
        logger.warning("Skip review session indexing for session=%s: %s", session_id, e)
        raise
    except Exception:
        logger.error(
            f"Failed to index review session {session_id}: {traceback.format_exc()}"
        )
        raise self.retry(countdown=120, max_retries=2)


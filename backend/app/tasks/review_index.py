"""Celery task that orchestrates full indexing for a review session.

Usage:
    index_review_session_data.delay(session_id="<review-session-unique-id>")
    index_review_session_data.delay(session_id="...", kb_id=3)
"""

import traceback
from typing import List, Optional

from celery.utils.log import get_task_logger
from sqlmodel import Session, select

from app.celery import app as celery_app
from app.core.config import settings
from app.core.db import engine
from app.models import Document as DBDocument
from app.rag.datasource.review import ReviewDataSource
from app.rag.types import CrmDataType
from app.rag.chat.review.indexing_policy import (
    get_or_create_review_datasource_id,
    normalize_review_data_types,
    validate_review_index_scope_by_stage,
)
from app.repositories import crm_review_session_repo, knowledge_base_repo
from app.tasks.build_crm_index import build_crm_graph_index_for_document
from app.tasks.build_index import build_index_for_document

logger = get_task_logger(__name__)

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
        ``settings.CRM_DAILY_KB_ID`` when not provided.
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

            doc_count = 0
            for document in loader.load_documents():
                existing_id = db_session.exec(
                    select(DBDocument.id).where(
                        DBDocument.knowledge_base_id == kb.id,
                        DBDocument.data_source_id == data_source_id,
                        DBDocument.source_uri == document.source_uri,
                    )
                ).first()
                if existing_id:
                    logger.info(
                        f"Document already exists for source_uri={document.source_uri}, "
                        f"reusing document #{existing_id}"
                    )
                    build_index_for_document.delay(kb.id, existing_id)
                    build_crm_graph_index_for_document.delay(kb.id, existing_id)
                    doc_count += 1
                    continue

                db_session.add(document)
                db_session.commit()
                db_session.refresh(document)

                build_index_for_document.delay(kb.id, document.id)
                build_crm_graph_index_for_document.delay(kb.id, document.id)
                doc_count += 1

        logger.info(
            f"Review session indexing complete for session={session_id}: "
            f"{doc_count} documents queued for vector + KG indexing"
        )
    except ValueError as e:
        logger.warning("Skip review session indexing for session=%s: %s", session_id, e)
        raise
    except Exception:
        logger.error(
            f"Failed to index review session {session_id}: {traceback.format_exc()}"
        )
        raise self.retry(countdown=120, max_retries=2)


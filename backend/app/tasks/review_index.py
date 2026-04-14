"""Celery task that orchestrates full indexing for a review session.

Usage:
    index_review_session_data.delay(session_id="<review-session-unique-id>")
    index_review_session_data.delay(session_id="...", kb_id=3)
"""

import traceback
from datetime import datetime

from celery.utils.log import get_task_logger
from sqlmodel import Session, select

from app.celery import app as celery_app
from app.core.config import settings
from app.core.db import engine
from app.models import Document as DBDocument
from app.models.document import DocumentCategory
from app.models.data_source import DataSource, DataSourceType
from app.models.knowledge_base import KnowledgeBaseDataSource
from app.types import MimeTypes
from app.rag.datasource.review import ReviewDataSource
from app.repositories import knowledge_base_repo
from app.repositories.crm_review_session import crm_review_session_repo
from app.tasks.build_crm_index import build_crm_graph_index_for_document
from app.tasks.build_index import build_index_for_document

logger = get_task_logger(__name__)

REVIEW_DATASOURCE_NAME = "CRM Review Sessions"


@celery_app.task(bind=True)
def index_review_session_data(self, session_id: str, kb_id: int = None):
    """Index all review data for a given session into vector store and KG.

    Parameters
    ----------
    session_id : str
        The ``unique_id`` of the review session to index.
    kb_id : int | None
        Knowledge base to store documents in.  Falls back to
        ``settings.CRM_DAILY_KB_ID`` when not provided.
    """
    kb_id = kb_id or settings.CRM_DAILY_KB_ID
    logger.info(f"Starting review session indexing for session={session_id}, kb={kb_id}")

    try:
        with Session(engine, expire_on_commit=False) as db_session:
            kb = knowledge_base_repo.must_get(db_session, kb_id)

            data_source_id = _get_or_create_review_datasource_id(db_session, kb)

            loader = ReviewDataSource(
                db_session=db_session,
                knowledge_base_id=kb.id,
                data_source_id=data_source_id,
                user_id=None,
                review_session_id=session_id,
            )

            doc_count = 0
            for document in loader.load_documents():
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
    except Exception:
        logger.error(
            f"Failed to index review session {session_id}: {traceback.format_exc()}"
        )
        raise self.retry(countdown=120, max_retries=2)


def _get_or_create_review_datasource_id(db_session: Session, kb) -> int:
    """Find an existing review-dedicated datasource in the KB, or create one.

    We look for a CRM-type datasource named ``REVIEW_DATASOURCE_NAME``.
    If none exists we create it and link it to the knowledge base.
    """
    for ds in kb.data_sources:
        if ds.name == REVIEW_DATASOURCE_NAME and not ds.deleted_at:
            return ds.id

    ds = DataSource(
        name=REVIEW_DATASOURCE_NAME,
        description="Auto-created datasource for review session indexing",
        data_source_type=DataSourceType.CRM,
        config=[],
    )
    db_session.add(ds)
    db_session.flush()

    link = KnowledgeBaseDataSource(
        knowledge_base_id=kb.id,
        data_source_id=ds.id,
    )
    db_session.add(link)
    db_session.commit()
    db_session.refresh(ds)

    logger.info(f"Created review datasource id={ds.id} for kb={kb.id}")
    return ds.id


@celery_app.task(bind=True)
def index_review_recommendation_feedback(
    self,
    *,
    session_id: str,
    recommendation_id: str,
    recommendation: dict,
    kb_id: int = None,
):
    """Index recommendation feedback into vector/KG for future reranking."""
    kb_id = kb_id or settings.CRM_DAILY_KB_ID
    try:
        with Session(engine, expire_on_commit=False) as db_session:
            kb = knowledge_base_repo.must_get(db_session, kb_id)
            review_session = crm_review_session_repo.get_by_unique_id(db_session, session_id)
            if not review_session:
                logger.warning(
                    "Skip recommendation indexing: review session %s not found", session_id
                )
                return

            data_source_id = _get_or_create_review_datasource_id(db_session, kb)
            now = datetime.now()
            content = (
                f"# Review Recommendation Feedback\n\n"
                f"- recommendation_id: {recommendation_id}\n"
                f"- session_id: {session_id}\n"
                f"- week_id: {review_session.period}\n"
                f"- status: {recommendation.get('status', '')}\n"
                f"- outcome: {recommendation.get('outcome', '')}\n"
                f"- action: {recommendation.get('action', '')}\n"
                f"- rationale: {recommendation.get('rationale', '')}\n"
                f"- notes: {recommendation.get('notes', '')}\n"
                f"- kpi_delta_after_1w: {recommendation.get('kpi_delta_after_1w', '')}\n"
            )
            source_uri = f"crm/review_recommendation/{session_id}/{recommendation_id}.md"

            existing_id = db_session.exec(
                select(DBDocument.id).where(
                    DBDocument.knowledge_base_id == kb.id,
                    DBDocument.data_source_id == data_source_id,
                    DBDocument.source_uri == source_uri,
                )
            ).first()
            if existing_id:
                build_index_for_document.delay(kb.id, existing_id)
                build_crm_graph_index_for_document.delay(kb.id, existing_id)
                return

            doc = DBDocument(
                name=f"review-recommendation-{recommendation_id}.md",
                hash=hash(content),
                content=content,
                mime_type=MimeTypes.MARKDOWN,
                knowledge_base_id=kb.id,
                data_source_id=data_source_id,
                source_uri=source_uri,
                created_at=now,
                updated_at=now,
                last_modified_at=now,
                meta={
                    "category": DocumentCategory.CRM,
                    "crm_data_type": "crm_review_recommendation",
                    "session_id": session_id,
                    "snapshot_period": review_session.period,
                    "week_id": review_session.period,
                    "week_start": str(review_session.period_start),
                    "week_end": str(review_session.period_end),
                    "time_granularity": "week",
                    "recommendation_id": recommendation_id,
                    "recommendation_status": recommendation.get("status", ""),
                    "recommendation_outcome": recommendation.get("outcome", ""),
                },
            )
            db_session.add(doc)
            db_session.commit()
            db_session.refresh(doc)
            build_index_for_document.delay(kb.id, doc.id)
            build_crm_graph_index_for_document.delay(kb.id, doc.id)
    except Exception:
        logger.error(
            "Failed to index review recommendation feedback: %s", traceback.format_exc()
        )
        raise self.retry(countdown=120, max_retries=2)

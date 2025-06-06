import traceback
from uuid import UUID
from sqlmodel import Session
from celery.utils.log import get_task_logger

from app.celery import app as celery_app
from app.core.db import engine
from app.models import (
    Document as DBDocument,
    DocumentCategory,
)
from app.models.chunk import PlaybookKgIndexStatus, get_kb_chunk_model
from app.rag.build_index import IndexService
from app.rag.knowledge_base.config import get_kb_llm, get_kb_embed_model
from app.repositories import knowledge_base_repo
from app.repositories.chunk import ChunkRepo

logger = get_task_logger(__name__)


@celery_app.task(bind=True)
def build_playbook_index_for_document(self, knowledge_base_id: int, document_id: int):
    # Pre-check before building index.
    with Session(engine, expire_on_commit=False) as session:
        kb = knowledge_base_repo.must_get(session, knowledge_base_id)

        # Check document.
        db_document = session.get(DBDocument, document_id)
        if db_document is None:
            logger.error(f"Document #{document_id} is not found")
            return

        if db_document.get_metadata().category != DocumentCategory.PLAYBOOK and db_document.get_metadata().category != DocumentCategory.CRM:
            logger.error(f"Document #{document_id} is not playbook or crm category")
            return
        
    
    # Build knowledge graph index for playbook.
    with Session(engine, expire_on_commit=False) as session:
        chunk_repo = ChunkRepo(get_kb_chunk_model(kb))
        chunks = chunk_repo.get_document_chunks(session, document_id)
        for chunk in chunks:
            build_playbook_kg_index_for_chunk.delay(knowledge_base_id, chunk.id)


@celery_app.task
def build_playbook_kg_index_for_chunk(knowledge_base_id: int, chunk_id: UUID):
    with Session(engine, expire_on_commit=False) as session:
        kb = knowledge_base_repo.must_get(session, knowledge_base_id)

        # Check chunk.
        chunk_model = get_kb_chunk_model(kb)
        db_chunk = session.get(chunk_model, chunk_id)
        if db_chunk is None:
            logger.error(f"Chunk #{chunk_id} is not found")
            return

        if db_chunk.playbook_index_status not in (
            PlaybookKgIndexStatus.PENDING,
            PlaybookKgIndexStatus.NOT_STARTED,
        ):
            logger.info(f"Chunk #{chunk_id} is not in pending state")
            return
        
        # Init knowledge base index service。
        llm = get_kb_llm(session, kb)
        embed_model = get_kb_embed_model(session, kb)
        index_service = IndexService(llm, embed_model, kb)

        db_chunk.playbook_index_status = PlaybookKgIndexStatus.RUNNING
        session.add(db_chunk)
        session.commit()

    try:
        with Session(engine) as playbook_index_session:
            index_service.build_playbook_kg_index_for_chunk(playbook_index_session, db_chunk)

        with Session(engine) as session:
            db_chunk.playbook_index_status = PlaybookKgIndexStatus.COMPLETED
            session.add(db_chunk)
            session.commit()
            logger.info(
                f"Built playbook knowledge graph index for chunk #{chunk_id} successfully."
            )
    except Exception:
        with Session(engine) as session:
            error_msg = traceback.format_exc()
            logger.error(
                f"Failed to build playbook knowledge graph index for chunk #{chunk_id}",
                exc_info=True,
            )
            db_chunk.playbook_index_status = PlaybookKgIndexStatus.FAILED
            db_chunk.playbook_index_result = error_msg
            session.add(db_chunk)
            session.commit()

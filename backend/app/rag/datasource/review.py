"""ReviewDataSource — loads review session data as Documents for vector / KG indexing.

Unlike ``CRMDataSource`` which is bound to a knowledge-base data-source lifecycle,
this class is designed to be invoked directly by the ``index_review_session_data``
Celery task with an explicit ``session_id``.
"""

import logging
from datetime import datetime
from typing import Dict, Generator, List, Optional

from sqlmodel import Session, select

from app.models import Document
from app.models.crm_review import (
    CRMReviewAttendee,
    CRMReviewKpiMetrics,
    CRMReviewOppBranchSnapshot,
    CRMReviewOppRiskProgress,
    CRMReviewSession,
)
from app.models.document import DocumentCategory
from app.rag.datasource.crm_to_file import save_crm_to_file
from app.rag.datasource.review_format import (
    format_review_session_info,
    format_risk_progress_info,
    format_snapshot_info,
)
from app.rag.types import CrmDataType
from app.types import MimeTypes

logger = logging.getLogger(__name__)


class ReviewDataSource:
    """Generates ``Document`` objects from review tables for a given session.

    Each call to ``load_documents`` yields:
    - 1 session-level document (session metadata + KPI summary)
    - N snapshot documents (one per opportunity branch snapshot)
    - M risk/progress documents (one per risk or progress record)
    """

    def __init__(
        self,
        db_session: Session,
        knowledge_base_id: int,
        data_source_id: int,
        user_id,
        review_session_id: str,
    ):
        self.db_session = db_session
        self.knowledge_base_id = knowledge_base_id
        self.data_source_id = data_source_id
        self.user_id = user_id
        self.review_session_id = review_session_id

    def load_documents(self) -> Generator[Document, None, None]:
        """Yield Documents for the configured review session."""
        session_obj = self._get_review_session()
        if session_obj is None:
            logger.warning(f"Review session {self.review_session_id} not found, skipping indexing")
            return

        # 1. Session + KPI document
        yield from self._load_session_document(session_obj)

        # 2. Snapshot documents
        yield from self._load_snapshot_documents(session_obj)

        # 3. Risk / Progress documents
        yield from self._load_risk_progress_documents(session_obj)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_review_session(self) -> Optional[CRMReviewSession]:
        stmt = select(CRMReviewSession).where(
            CRMReviewSession.unique_id == self.review_session_id
        )
        return self.db_session.exec(stmt).first()

    def _load_session_document(
        self, session_obj: CRMReviewSession
    ) -> Generator[Document, None, None]:
        kpi_metrics = self._get_kpi_metrics(session_obj.unique_id)
        lines = format_review_session_info(session_obj, kpi_metrics)
        content_str = "\n".join(lines)

        metadata = self._base_metadata(CrmDataType.REVIEW_SESSION)
        metadata["unique_id"] = session_obj.unique_id
        metadata["session_id"] = session_obj.unique_id
        metadata["department_id"] = session_obj.department_id
        metadata["department_name"] = session_obj.department_name or ""
        metadata["period"] = session_obj.period
        metadata["snapshot_period"] = session_obj.period
        metadata["stage"] = session_obj.stage

        doc_datetime = datetime.now()
        session_label = session_obj.session_name or f"{session_obj.department_name}_{session_obj.period}"
        file_name = f"{session_label}_{session_obj.unique_id}.md"
        upload = save_crm_to_file(
            "review_session", session_obj, content_str, doc_datetime, metadata,
            file_name=file_name,
        )

        yield Document(
            name=upload.name if upload else file_name,
            hash=hash(content_str),
            content=content_str,
            mime_type=MimeTypes.MARKDOWN,
            knowledge_base_id=self.knowledge_base_id,
            data_source_id=self.data_source_id,
            user_id=self.user_id,
            file_id=upload.id if upload else None,
            source_uri=upload.path if upload else f"crm/review_session/{session_obj.unique_id}.md",
            created_at=doc_datetime,
            updated_at=doc_datetime,
            last_modified_at=doc_datetime,
            meta=metadata,
        )

    def _get_session_attendee_owner_ids(self, session_id: str) -> List[str]:
        """Get crm_user_id list of all attendees for this session."""
        stmt = select(CRMReviewAttendee.crm_user_id).where(
            CRMReviewAttendee.session_id == session_id
        )
        return [row for row in self.db_session.exec(stmt).all() if row]

    def _load_snapshot_documents(
        self, session_obj: CRMReviewSession
    ) -> Generator[Document, None, None]:
        period = session_obj.period
        owner_ids = self._get_session_attendee_owner_ids(session_obj.unique_id)

        stmt = select(CRMReviewOppBranchSnapshot).where(
            CRMReviewOppBranchSnapshot.snapshot_period == period
        )
        if owner_ids:
            stmt = stmt.where(
                CRMReviewOppBranchSnapshot.owner_id.in_(owner_ids)
            )
        else:
            logger.warning(
                f"No attendees found for session {session_obj.unique_id}, "
                f"falling back to all snapshots in period {period}"
            )

        snapshots: List[CRMReviewOppBranchSnapshot] = list(self.db_session.exec(stmt).all())
        logger.info(
            f"Loading {len(snapshots)} snapshot documents for session "
            f"{session_obj.unique_id} (period={period}, attendee_owners={len(owner_ids)})"
        )

        for snap in snapshots:
            lines = format_snapshot_info(snap)
            content_str = "\n".join(lines)

            metadata = self._base_metadata(CrmDataType.REVIEW_SNAPSHOT)
            metadata["unique_id"] = snap.unique_id
            metadata["session_id"] = session_obj.unique_id
            metadata["opportunity_id"] = snap.opportunity_id
            metadata["account_id"] = snap.account_id or ""
            metadata["account_name"] = snap.account_name or ""
            metadata["owner_id"] = snap.owner_id
            metadata["owner_name"] = snap.owner_name or ""
            metadata["snapshot_period"] = snap.snapshot_period
            metadata["forecast_type"] = snap.forecast_type or ""
            metadata["opportunity_stage"] = snap.opportunity_stage or ""

            doc_datetime = datetime.now()
            snap_label = snap.opportunity_name or snap.opportunity_id
            file_name = f"{snap_label}_{snap.snapshot_period}_{snap.unique_id}.md"
            upload = save_crm_to_file(
                "review_snapshot", snap, content_str, doc_datetime, metadata,
                file_name=file_name,
            )

            yield Document(
                name=upload.name if upload else file_name,
                hash=hash(content_str),
                content=content_str,
                mime_type=MimeTypes.MARKDOWN,
                knowledge_base_id=self.knowledge_base_id,
                data_source_id=self.data_source_id,
                user_id=self.user_id,
                file_id=upload.id if upload else None,
                source_uri=upload.path if upload else f"crm/review_snapshot/{snap.unique_id}.md",
                created_at=doc_datetime,
                updated_at=doc_datetime,
                last_modified_at=doc_datetime,
                meta=metadata,
            )

    def _load_risk_progress_documents(
        self, session_obj: CRMReviewSession
    ) -> Generator[Document, None, None]:
        stmt = select(CRMReviewOppRiskProgress).where(
            CRMReviewOppRiskProgress.session_id == session_obj.unique_id
        )
        records: List[CRMReviewOppRiskProgress] = list(self.db_session.exec(stmt).all())
        logger.info(f"Loading {len(records)} risk/progress documents for session {session_obj.unique_id}")

        for rp in records:
            lines = format_risk_progress_info(rp)
            content_str = "\n".join(lines)

            metadata = self._base_metadata(CrmDataType.REVIEW_RISK_PROGRESS)
            metadata["unique_id"] = rp.unique_id
            metadata["session_id"] = session_obj.unique_id
            metadata["record_type"] = rp.record_type
            metadata["type_code"] = rp.type_code
            metadata["scope_type"] = rp.scope_type
            metadata["scope_id"] = rp.scope_id or ""
            metadata["snapshot_period"] = rp.snapshot_period
            metadata["calc_phase"] = rp.calc_phase
            if rp.opportunity_id:
                metadata["opportunity_id"] = rp.opportunity_id
            if rp.owner_id:
                metadata["owner_id"] = rp.owner_id

            doc_datetime = datetime.now()
            rp_label = rp.type_name or rp.type_code
            file_name = f"{rp_label}_{rp.snapshot_period}_{rp.unique_id}.md"
            upload = save_crm_to_file(
                "review_risk_progress", rp, content_str, doc_datetime, metadata,
                file_name=file_name,
            )

            yield Document(
                name=upload.name if upload else file_name,
                hash=hash(content_str),
                content=content_str,
                mime_type=MimeTypes.MARKDOWN,
                knowledge_base_id=self.knowledge_base_id,
                data_source_id=self.data_source_id,
                user_id=self.user_id,
                file_id=upload.id if upload else None,
                source_uri=upload.path if upload else f"crm/review_risk_progress/{rp.unique_id}.md",
                created_at=doc_datetime,
                updated_at=doc_datetime,
                last_modified_at=doc_datetime,
                meta=metadata,
            )

    def _get_kpi_metrics(self, session_id: str) -> List[CRMReviewKpiMetrics]:
        stmt = select(CRMReviewKpiMetrics).where(
            CRMReviewKpiMetrics.session_id == session_id
        )
        return list(self.db_session.exec(stmt).all())

    def _base_metadata(self, crm_data_type: CrmDataType) -> Dict:
        return {
            "category": DocumentCategory.CRM,
            "crm_data_type": crm_data_type,
        }

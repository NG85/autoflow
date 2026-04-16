"""ReviewDataSource — loads review session data as Documents for vector / KG indexing.

Unlike ``CRMDataSource`` which is bound to a knowledge-base data-source lifecycle,
this class is designed to be invoked directly by the ``index_review_session_data``
Celery task with an explicit ``session_id``.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Set

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
from app.repositories.department_mirror import department_mirror_repo
from app.repositories.user_department_relation import user_department_relation_repo
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
        include_data_types: Optional[List[CrmDataType]] = None,
    ):
        self.db_session = db_session
        self.knowledge_base_id = knowledge_base_id
        self.data_source_id = data_source_id
        self.user_id = user_id
        self.review_session_id = review_session_id
        self.include_data_types: Set[CrmDataType] = set(
            include_data_types
            or [
                CrmDataType.REVIEW_SESSION,
                CrmDataType.REVIEW_SNAPSHOT,
                CrmDataType.REVIEW_RISK_PROGRESS,
            ]
        )

    def load_documents(self) -> Generator[Document, None, None]:
        """Yield Documents for the configured review session."""
        session_obj = self._get_review_session()
        if session_obj is None:
            logger.warning(f"Review session {self.review_session_id} not found, skipping indexing")
            return

        # 1. Session + KPI document
        if CrmDataType.REVIEW_SESSION in self.include_data_types:
            yield from self._load_session_document(session_obj)

        # 2. Snapshot documents
        if CrmDataType.REVIEW_SNAPSHOT in self.include_data_types:
            yield from self._load_snapshot_documents(session_obj)

        # 3. Risk / Progress documents
        if CrmDataType.REVIEW_RISK_PROGRESS in self.include_data_types:
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
        kpi_metrics = self._get_kpi_metrics(session_obj)
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

    def _get_session_attendee_owner_name_map(self, session_id: str) -> Dict[str, str]:
        """Get attendee crm_user_id -> user_name map for this session."""
        stmt = select(CRMReviewAttendee.crm_user_id, CRMReviewAttendee.user_name).where(
            CRMReviewAttendee.session_id == session_id
        )
        rows = self.db_session.exec(stmt).all()
        out: Dict[str, str] = {}
        for crm_user_id, user_name in rows:
            uid = str(crm_user_id or "").strip()
            uname = str(user_name or "").strip()
            if uid and uname and uid not in out:
                out[uid] = uname
        return out

    def _load_snapshot_documents(
        self, session_obj: CRMReviewSession
    ) -> Generator[Document, None, None]:
        period = session_obj.period
        owner_ids = self._get_session_attendee_owner_ids(session_obj.unique_id)

        if not owner_ids:
            logger.warning(
                f"No attendees found for session {session_obj.unique_id}, "
                f"skipping snapshot loading to avoid indexing unrelated data"
            )
            return

        stmt = select(CRMReviewOppBranchSnapshot).where(
            CRMReviewOppBranchSnapshot.snapshot_period == period,
            CRMReviewOppBranchSnapshot.owner_id.in_(owner_ids),
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

    def _build_scope_name_maps(
        self, session_obj: CRMReviewSession
    ) -> Dict[str, Dict[str, str]]:
        """Build {scope_type: {id: name}} maps from snapshots and session.

        Used to resolve ``CRMReviewOppRiskProgress.scope_id`` by ``scope_type``:
        ``opportunity`` → 商机, ``owner`` → 负责人, ``department`` → 部门.
        ``company`` scope has no ``scope_id`` and is not listed here.
        """
        opp_map: Dict[str, str] = {}
        owner_map: Dict[str, str] = {}

        stmt = select(
            CRMReviewOppBranchSnapshot.opportunity_id,
            CRMReviewOppBranchSnapshot.opportunity_name,
            CRMReviewOppBranchSnapshot.owner_id,
            CRMReviewOppBranchSnapshot.owner_name,
            CRMReviewOppBranchSnapshot.owner_department_id,
            CRMReviewOppBranchSnapshot.owner_department_name,
        ).where(
            CRMReviewOppBranchSnapshot.snapshot_period == session_obj.period,
        )
        dept_map: Dict[str, str] = {}
        if session_obj.department_id and session_obj.department_name:
            dept_map[session_obj.department_id] = session_obj.department_name

        for row in self.db_session.exec(stmt).all():
            if row[0] and row[1]:
                opp_map[row[0]] = row[1]
            if row[2] and row[3]:
                owner_map[row[2]] = row[3]
            od_id, od_name = row[4], row[5]
            if od_id and od_name:
                dept_map[od_id] = od_name

        return {
            "opportunity": opp_map,
            "owner": owner_map,
            "department": dept_map,
        }

    def _load_risk_progress_documents(
        self, session_obj: CRMReviewSession
    ) -> Generator[Document, None, None]:
        stmt = select(CRMReviewOppRiskProgress).where(
            CRMReviewOppRiskProgress.session_id == session_obj.unique_id
        )
        records: List[CRMReviewOppRiskProgress] = list(self.db_session.exec(stmt).all())
        logger.info(f"Loading {len(records)} risk/progress documents for session {session_obj.unique_id}")

        scope_maps = self._build_scope_name_maps(session_obj)
        opp_name_map = scope_maps["opportunity"]
        owner_name_map = scope_maps["owner"]
        dept_name_map = scope_maps["department"]

        for rp in records:
            resolved_scope_name = None
            if rp.scope_id and rp.scope_type:
                type_map = scope_maps.get(rp.scope_type, {})
                resolved_scope_name = type_map.get(rp.scope_id)

            owner_nm = (
                owner_name_map.get(rp.owner_id) if rp.owner_id else None
            )
            dept_nm = (
                dept_name_map.get(rp.department_id) if rp.department_id else None
            )

            lines = format_risk_progress_info(
                rp,
                opportunity_name=opp_name_map.get(rp.opportunity_id) if rp.opportunity_id else None,
                scope_name=resolved_scope_name,
                owner_name=owner_nm,
                department_name=dept_nm,
            )
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

    def _get_kpi_metrics(self, session_obj: CRMReviewSession) -> List[Dict[str, Any]]:
        """
        Fetch and enrich KPI metrics for review session docs.

        Rules:
        - scope_type only includes department / owner.
        - scope_id means department_id (department scope) or crm_user_id (owner scope).
        - scope_name should be human-readable.
        """
        session_id = session_obj.unique_id
        stmt = (
            select(CRMReviewKpiMetrics)
            .where(CRMReviewKpiMetrics.session_id == session_id)
            .where(CRMReviewKpiMetrics.scope_type.in_(["department", "owner"]))
            .order_by(
                CRMReviewKpiMetrics.scope_type,
                CRMReviewKpiMetrics.scope_id,
                CRMReviewKpiMetrics.metric_name,
            )
        )
        rows: List[CRMReviewKpiMetrics] = list(self.db_session.exec(stmt).all())
        if not rows:
            return []

        owner_ids = {
            str(r.scope_id).strip()
            for r in rows
            if r.scope_type == "owner" and r.scope_id
        }
        dept_ids = {
            str(r.scope_id).strip()
            for r in rows
            if r.scope_type == "department" and r.scope_id
        }
        owner_name_map = (
            user_department_relation_repo.get_user_names_by_crm_user_ids(
                self.db_session, owner_ids
            )
            if owner_ids
            else {}
        )
        attendee_owner_name_map = self._get_session_attendee_owner_name_map(session_id)
        dept_name_map = (
            department_mirror_repo.get_department_names_by_ids(self.db_session, dept_ids)
            if dept_ids
            else {}
        )
        if session_obj.department_id and session_obj.department_name:
            dept_name_map.setdefault(
                str(session_obj.department_id).strip(),
                session_obj.department_name,
            )

        out: List[Dict[str, Any]] = []
        for r in rows:
            scope_id = str(r.scope_id or "").strip()
            scope_name = (r.scope_name or "").strip()
            if not scope_name:
                if r.scope_type == "owner" and scope_id:
                    scope_name = (
                        attendee_owner_name_map.get(scope_id)
                        or owner_name_map.get(scope_id)
                        or scope_id
                    )
                elif r.scope_type == "department" and scope_id:
                    scope_name = dept_name_map.get(scope_id, scope_id)
            out.append(
                {
                    "scope_type": r.scope_type,
                    "scope_id": scope_id,
                    "scope_name": scope_name,
                    "metric_category": r.metric_category,
                    "metric_name": r.metric_name,
                    "metric_value": r.metric_value,
                    "metric_value_prev": r.metric_value_prev,
                    "metric_delta": r.metric_delta,
                    "metric_rate": r.metric_rate,
                    "metric_unit": r.metric_unit,
                    "metric_content": r.metric_content,
                    "calc_phase": r.calc_phase,
                }
            )
        return out

    def _base_metadata(self, crm_data_type: CrmDataType) -> Dict:
        return {
            "category": DocumentCategory.CRM,
            "crm_data_type": crm_data_type,
        }

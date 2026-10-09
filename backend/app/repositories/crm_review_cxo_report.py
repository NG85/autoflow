from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.models.crm_review import CRMReviewCxoReport
from app.repositories.base_repo import BaseRepo


class CRMReviewCxoReportRepo(BaseRepo):
    model_cls = CRMReviewCxoReport

    def get_by_session_id(self, db_session: Session, session_id: str) -> Optional[CRMReviewCxoReport]:
        if not session_id:
            return None
        return db_session.exec(
            select(CRMReviewCxoReport).where(CRMReviewCxoReport.session_id == session_id)
        ).first()


crm_review_cxo_report_repo = CRMReviewCxoReportRepo()

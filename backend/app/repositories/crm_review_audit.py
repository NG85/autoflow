from __future__ import annotations

from typing import List

from sqlmodel import Session, select

from app.models.crm_review import CRMReviewOppAuditLog
from app.repositories.base_repo import BaseRepo


class CRMReviewOppAuditLogRepo(BaseRepo):
    model_cls = CRMReviewOppAuditLog

    def create_audit(self, db_session: Session, audit: CRMReviewOppAuditLog) -> CRMReviewOppAuditLog:
        db_session.add(audit)
        db_session.commit()
        db_session.refresh(audit)
        return audit

    def list_by_session_id(self, db_session: Session, *, session_id: str) -> List[CRMReviewOppAuditLog]:
        return list(
            db_session.exec(
                select(CRMReviewOppAuditLog)
                .where(CRMReviewOppAuditLog.session_id == session_id)
                .order_by(CRMReviewOppAuditLog.updated_at.desc())
            ).all()
        )


crm_review_opp_audit_log_repo = CRMReviewOppAuditLogRepo()


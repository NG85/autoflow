from __future__ import annotations

from sqlmodel import Session

from app.models.crm_review import CRMReviewOppAuditLog
from app.repositories.base_repo import BaseRepo


class CRMReviewOppAuditLogRepo(BaseRepo):
    model_cls = CRMReviewOppAuditLog

    def create_audit(self, db_session: Session, audit: CRMReviewOppAuditLog) -> CRMReviewOppAuditLog:
        db_session.add(audit)
        db_session.commit()
        db_session.refresh(audit)
        return audit


crm_review_opp_audit_log_repo = CRMReviewOppAuditLogRepo()


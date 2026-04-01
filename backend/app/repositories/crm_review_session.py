from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.models.crm_review import CRMReviewSession
from app.repositories.base_repo import BaseRepo


class CRMReviewSessionRepo(BaseRepo):
    model_cls = CRMReviewSession

    def get_by_unique_id(self, db_session: Session, unique_id: str) -> Optional[CRMReviewSession]:
        if not unique_id:
            return None
        return db_session.exec(
            select(CRMReviewSession).where(CRMReviewSession.unique_id == unique_id)
        ).first()


crm_review_session_repo = CRMReviewSessionRepo()


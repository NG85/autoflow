from __future__ import annotations

from typing import Optional, List

from sqlmodel import Session, select, func

from app.models.crm_review import CRMReviewAttendee
from app.repositories.base_repo import BaseRepo


class CRMReviewAttendeeRepo(BaseRepo):
    model_cls = CRMReviewAttendee

    def get_by_session_and_user_id(
        self, db_session: Session, *, session_id: str, user_id: str
    ) -> Optional[CRMReviewAttendee]:
        if not session_id or not user_id:
            return None
        return db_session.exec(
            select(CRMReviewAttendee).where(
                CRMReviewAttendee.session_id == session_id,
                CRMReviewAttendee.user_id == user_id,
            )
        ).first()

    def get_submit_stats(self, db_session: Session, *, session_id: str) -> dict:
        """
        Returns:
          { total, submitted, not_submitted }
        """
        if not session_id:
            return {"total": 0, "submitted": 0, "not_submitted": 0}

        total = db_session.exec(
            select(func.count()).select_from(CRMReviewAttendee).where(CRMReviewAttendee.session_id == session_id)
        ).one()
        submitted = db_session.exec(
            select(func.count())
            .select_from(CRMReviewAttendee)
            .where(
                CRMReviewAttendee.session_id == session_id,
                CRMReviewAttendee.has_submitted == True,  # noqa: E712
            )
        ).one()
        not_submitted = int(total) - int(submitted)
        return {"total": int(total), "submitted": int(submitted), "not_submitted": int(not_submitted)}

    def get_crm_user_ids_by_session(self, db_session: Session, *, session_id: str) -> List[str]:
        """
        Return distinct crm_user_id list for a review session.
        """
        if not session_id:
            return []
        rows = db_session.exec(
            select(CRMReviewAttendee.crm_user_id).where(
                CRMReviewAttendee.session_id == session_id,
                CRMReviewAttendee.crm_user_id.is_not(None),
            ).distinct()
        ).all()
        return [str(r) for r in rows if r]


crm_review_attendee_repo = CRMReviewAttendeeRepo()


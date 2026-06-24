from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, List

from sqlmodel import Session, select

from app.models.crm_review import CRMReviewAttendee
from app.repositories.base_repo import BaseRepo


def _serialize_submit_attendee(row: CRMReviewAttendee) -> dict:
    return {
        "user_id": str(row.user_id or "").strip(),
        "crm_user_id": str(row.crm_user_id or "").strip(),
        "user_name": row.user_name,
        "department_id": row.department_id,
        "department_name": row.department_name,
        "is_leader": bool(row.is_leader),
        "has_submitted": bool(row.has_submitted),
        "submitted_at": row.submitted_at,
    }


def _submitted_attendee_sort_key(attendee: dict[str, Any]) -> tuple[Any, ...]:
    submitted_at = attendee.get("submitted_at")
    user_name = str(attendee.get("user_name") or "").casefold()
    if isinstance(submitted_at, datetime):
        return (submitted_at.timestamp(), user_name)
    return (float("inf"), user_name)


def _not_submitted_attendee_sort_key(attendee: dict[str, Any]) -> str:
    return str(attendee.get("user_name") or "").casefold()


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
          { total, submitted, not_submitted, submitted_attendees, not_submitted_attendees }
        """
        empty = {
            "total": 0,
            "submitted": 0,
            "not_submitted": 0,
            "submitted_attendees": [],
            "not_submitted_attendees": [],
        }
        if not session_id:
            return empty

        rows = db_session.exec(
            select(CRMReviewAttendee).where(CRMReviewAttendee.session_id == session_id)
        ).all()
        attendees = [_serialize_submit_attendee(row) for row in rows]
        submitted_attendees = [a for a in attendees if a["has_submitted"]]
        not_submitted_attendees = [a for a in attendees if not a["has_submitted"]]
        submitted_attendees.sort(key=_submitted_attendee_sort_key)
        not_submitted_attendees.sort(key=_not_submitted_attendee_sort_key)
        return {
            "total": len(attendees),
            "submitted": len(submitted_attendees),
            "not_submitted": len(not_submitted_attendees),
            "submitted_attendees": submitted_attendees,
            "not_submitted_attendees": not_submitted_attendees,
        }

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


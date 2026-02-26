from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from zoneinfo import ZoneInfo

from app.models.crm_weekly_followup_leader_engagement import CRMWeeklyFollowupLeaderEngagement
from app.models.crm_weekly_followup_summary import CRMWeeklyFollowupSummary

logger = logging.getLogger(__name__)

_DEFAULT_TZ = ZoneInfo("Asia/Shanghai")


def _ensure_tz_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    统一 datetime 的时区形态，避免 naive/aware 混用导致比较异常。
    约定：若传入 naive datetime，则视为北京时间（Asia/Shanghai）。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_DEFAULT_TZ)
    return dt


class CRMWeeklyFollowupEngagementService:
    def upsert_engagement(
        self,
        session: Session,
        *,
        summary: CRMWeeklyFollowupSummary,
        leader_user_id: str,
        reviewed_at: Optional[datetime] = None,
        commented_at: Optional[datetime] = None,
    ) -> CRMWeeklyFollowupLeaderEngagement:
        """
        幂等 upsert：按 (summary_id, leader_user_id) 唯一键写入/更新。

        规则：reviewed_at/commented_at 仅做“向后推进”（取 max）。
        """
        leader_user_id = (leader_user_id or "").strip()
        if not leader_user_id:
            raise ValueError("leader_user_id is required")

        reviewed_at = _ensure_tz_aware(reviewed_at)
        commented_at = _ensure_tz_aware(commented_at)

        existing = session.exec(
            select(CRMWeeklyFollowupLeaderEngagement).where(
                CRMWeeklyFollowupLeaderEngagement.summary_id == summary.id,
                CRMWeeklyFollowupLeaderEngagement.leader_user_id == leader_user_id,
            )
        ).first()

        if existing:
            changed = False
            existing_reviewed_at = _ensure_tz_aware(getattr(existing, "reviewed_at", None))
            existing_commented_at = _ensure_tz_aware(getattr(existing, "commented_at", None))

            if reviewed_at is not None and (existing_reviewed_at is None or reviewed_at > existing_reviewed_at):
                existing.reviewed_at = reviewed_at
                changed = True
            if commented_at is not None and (existing_commented_at is None or commented_at > existing_commented_at):
                existing.commented_at = commented_at
                changed = True
            if changed:
                session.add(existing)
                session.commit()
                session.refresh(existing)
            return existing

        obj = CRMWeeklyFollowupLeaderEngagement(
            summary_id=summary.id,
            week_start=summary.week_start,
            week_end=summary.week_end,
            department_id=(summary.department_id or "").strip(),
            department_name=summary.department_name or "",
            leader_user_id=leader_user_id,
            reviewed_at=reviewed_at,
            commented_at=commented_at,
        )
        try:
            session.add(obj)
            session.commit()
            session.refresh(obj)
            return obj
        except IntegrityError:
            # 并发兜底：再查一次并更新
            session.rollback()
            existing = session.exec(
                select(CRMWeeklyFollowupLeaderEngagement).where(
                    CRMWeeklyFollowupLeaderEngagement.summary_id == summary.id,
                    CRMWeeklyFollowupLeaderEngagement.leader_user_id == leader_user_id,
                )
            ).first()
            if not existing:
                raise
            changed = False
            existing_reviewed_at = _ensure_tz_aware(getattr(existing, "reviewed_at", None))
            existing_commented_at = _ensure_tz_aware(getattr(existing, "commented_at", None))

            if reviewed_at is not None and (existing_reviewed_at is None or reviewed_at > existing_reviewed_at):
                existing.reviewed_at = reviewed_at
                changed = True
            if commented_at is not None and (existing_commented_at is None or commented_at > existing_commented_at):
                existing.commented_at = commented_at
                changed = True
            if changed:
                session.add(existing)
                session.commit()
                session.refresh(existing)
            return existing


crm_weekly_followup_engagement_service = CRMWeeklyFollowupEngagementService()


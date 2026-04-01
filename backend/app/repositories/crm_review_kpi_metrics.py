from __future__ import annotations

from typing import List, Optional

from sqlmodel import Session, select

from app.models.crm_review import CRMReviewKpiMetrics
from app.repositories.base_repo import BaseRepo


class CRMReviewKpiMetricsRepo(BaseRepo):
    model_cls = CRMReviewKpiMetrics

    def list_by_session(
        self,
        db_session: Session,
        *,
        session_id: str,
        scope_type: Optional[str] = None,
        calc_phase: Optional[str] = None,
    ) -> List[CRMReviewKpiMetrics]:
        if not session_id:
            return []
        stmt = select(CRMReviewKpiMetrics).where(CRMReviewKpiMetrics.session_id == session_id)
        if scope_type:
            stmt = stmt.where(CRMReviewKpiMetrics.scope_type == scope_type)
        if calc_phase:
            stmt = stmt.where(CRMReviewKpiMetrics.calc_phase == calc_phase)
        stmt = stmt.order_by(
            CRMReviewKpiMetrics.scope_type,
            CRMReviewKpiMetrics.metric_category,
            CRMReviewKpiMetrics.metric_name,
        )
        return db_session.exec(stmt).all()


crm_review_kpi_metrics_repo = CRMReviewKpiMetricsRepo()


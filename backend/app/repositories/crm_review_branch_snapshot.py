from __future__ import annotations

from datetime import date
from typing import Any, Iterable, List, Optional

from sqlmodel import Session, select, func

from app.models.crm_review import CRMReviewOppBranchSnapshot, CRMReviewOppBranchSnapshotCache
from app.repositories.base_repo import BaseRepo


def _period_date_filters(
    model_cls: Any,
    *,
    snapshot_period: str,
    snapshot_date: Optional[date] = None,
) -> List[Any]:
    """Branch identity is (period, snapshot_date) under short sessions.

    When snapshot_date is None (legacy long session), keep period-only filter.
    """
    filters: List[Any] = [model_cls.snapshot_period == snapshot_period]
    if snapshot_date is not None:
        filters.append(model_cls.snapshot_date == snapshot_date)
    return filters


class CRMReviewOppBranchSnapshotRepo(BaseRepo):
    model_cls = CRMReviewOppBranchSnapshot

    def count_by_owner_and_period(
        self,
        db_session: Session,
        *,
        owner_crm_user_id: str,
        snapshot_period: str,
        snapshot_date: Optional[date] = None,
    ) -> int:
        if not owner_crm_user_id or not snapshot_period:
            return 0
        M = self.model_cls
        return int(
            db_session.exec(
                select(func.count()).where(
                    M.owner_id == owner_crm_user_id,
                    *_period_date_filters(
                        M, snapshot_period=snapshot_period, snapshot_date=snapshot_date
                    ),
                )
            ).one()
        )

    def list_by_owner_and_period_paginated(
        self,
        db_session: Session,
        *,
        owner_crm_user_id: str,
        snapshot_period: str,
        offset: int,
        limit: int,
        snapshot_date: Optional[date] = None,
    ) -> List[Any]:
        if not owner_crm_user_id or not snapshot_period:
            return []
        if limit <= 0:
            return []
        offset = max(offset, 0)
        M = self.model_cls
        return db_session.exec(
            select(M)
            .where(
                M.owner_id == owner_crm_user_id,
                *_period_date_filters(
                    M, snapshot_period=snapshot_period, snapshot_date=snapshot_date
                ),
            )
            .offset(offset)
            .limit(limit)
        ).all()

    def count_by_owner_ids_and_period(
        self,
        db_session: Session,
        *,
        owner_crm_user_ids: Iterable[str],
        snapshot_period: str,
        snapshot_date: Optional[date] = None,
    ) -> int:
        ids = [str(x).strip() for x in (owner_crm_user_ids or []) if x and str(x).strip()]
        if not ids or not snapshot_period:
            return 0
        M = self.model_cls
        return int(
            db_session.exec(
                select(func.count()).where(
                    M.owner_id.in_(ids),
                    *_period_date_filters(
                        M, snapshot_period=snapshot_period, snapshot_date=snapshot_date
                    ),
                )
            ).one()
        )

    def list_by_owner_ids_and_period_paginated(
        self,
        db_session: Session,
        *,
        owner_crm_user_ids: Iterable[str],
        snapshot_period: str,
        offset: int,
        limit: int,
        forecast_type_rank_case: Any,
        snapshot_date: Optional[date] = None,
    ) -> List[Any]:
        ids = [str(x).strip() for x in (owner_crm_user_ids or []) if x and str(x).strip()]
        if not ids or not snapshot_period or limit <= 0:
            return []
        offset = max(offset, 0)
        M = self.model_cls
        return db_session.exec(
            select(M)
            .where(
                M.owner_id.in_(ids),
                *_period_date_filters(
                    M, snapshot_period=snapshot_period, snapshot_date=snapshot_date
                ),
            )
            .order_by(
                func.coalesce(M.owner_name, ""),
                forecast_type_rank_case,
                M.forecast_amount.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()

    def get_by_owner_period_and_snapshot_unique_ids(
        self,
        db_session: Session,
        *,
        owner_crm_user_id: str,
        snapshot_period: str,
        snapshot_unique_ids: Iterable[str],
        snapshot_date: Optional[date] = None,
    ) -> List[Any]:
        ids = [str(x).strip() for x in (snapshot_unique_ids or []) if x and str(x).strip()]
        if not owner_crm_user_id or not snapshot_period or not ids:
            return []
        M = self.model_cls
        return db_session.exec(
            select(M).where(
                M.owner_id == owner_crm_user_id,
                *_period_date_filters(
                    M, snapshot_period=snapshot_period, snapshot_date=snapshot_date
                ),
                M.unique_id.in_(ids),
            )
        ).all()

    def get_by_owner_ids_period_and_snapshot_unique_ids(
        self,
        db_session: Session,
        *,
        owner_crm_user_ids: Iterable[str],
        snapshot_period: str,
        snapshot_unique_ids: Iterable[str],
        snapshot_date: Optional[date] = None,
    ) -> List[Any]:
        ids = [str(x).strip() for x in (snapshot_unique_ids or []) if x and str(x).strip()]
        owner_ids = [str(x).strip() for x in (owner_crm_user_ids or []) if x and str(x).strip()]
        if not owner_ids or not snapshot_period or not ids:
            return []
        M = self.model_cls
        return db_session.exec(
            select(M).where(
                M.owner_id.in_(owner_ids),
                *_period_date_filters(
                    M, snapshot_period=snapshot_period, snapshot_date=snapshot_date
                ),
                M.unique_id.in_(ids),
            )
        ).all()


crm_review_opp_branch_snapshot_repo = CRMReviewOppBranchSnapshotRepo()


class CRMReviewOppBranchSnapshotCacheRepo(CRMReviewOppBranchSnapshotRepo):
    model_cls = CRMReviewOppBranchSnapshotCache


crm_review_opp_branch_snapshot_cache_repo = CRMReviewOppBranchSnapshotCacheRepo()

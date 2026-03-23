from __future__ import annotations

from typing import Iterable, List

from sqlmodel import Session, select, func

from app.models.crm_review import CRMReviewOppBranchSnapshot
from app.repositories.base_repo import BaseRepo


class CRMReviewOppBranchSnapshotRepo(BaseRepo):
    model_cls = CRMReviewOppBranchSnapshot

    def count_by_owner_and_period(
        self, db_session: Session, *, owner_crm_user_id: str, snapshot_period: str
    ) -> int:
        if not owner_crm_user_id or not snapshot_period:
            return 0
        # count(*) over the specific owner+period scope
        return int(
            db_session.exec(
                select(func.count()).where(
                    CRMReviewOppBranchSnapshot.owner_id == owner_crm_user_id,
                    CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
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
    ) -> List[CRMReviewOppBranchSnapshot]:
        if not owner_crm_user_id or not snapshot_period:
            return []
        if limit <= 0:
            return []
        offset = max(offset, 0)
        return db_session.exec(
            select(CRMReviewOppBranchSnapshot).where(
                CRMReviewOppBranchSnapshot.owner_id == owner_crm_user_id,
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
            ).offset(offset).limit(limit)
        ).all()

    def count_by_owner_ids_and_period(
        self,
        db_session: Session,
        *,
        owner_crm_user_ids: Iterable[str],
        snapshot_period: str,
    ) -> int:
        ids = [str(x).strip() for x in (owner_crm_user_ids or []) if x and str(x).strip()]
        if not ids or not snapshot_period:
            return 0
        return int(
            db_session.exec(
                select(func.count()).where(
                    CRMReviewOppBranchSnapshot.owner_id.in_(ids),
                    CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
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
    ) -> List[CRMReviewOppBranchSnapshot]:
        ids = [str(x).strip() for x in (owner_crm_user_ids or []) if x and str(x).strip()]
        if not ids or not snapshot_period or limit <= 0:
            return []
        offset = max(offset, 0)
        return db_session.exec(
            select(CRMReviewOppBranchSnapshot).where(
                CRMReviewOppBranchSnapshot.owner_id.in_(ids),
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
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
    ) -> List[CRMReviewOppBranchSnapshot]:
        ids = [str(x).strip() for x in (snapshot_unique_ids or []) if x and str(x).strip()]
        if not owner_crm_user_id or not snapshot_period or not ids:
            return []
        return db_session.exec(
            select(CRMReviewOppBranchSnapshot).where(
                CRMReviewOppBranchSnapshot.owner_id == owner_crm_user_id,
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
                CRMReviewOppBranchSnapshot.unique_id.in_(ids),
            )
        ).all()

    def get_by_owner_ids_period_and_snapshot_unique_ids(
        self,
        db_session: Session,
        *,
        owner_crm_user_ids: Iterable[str],
        snapshot_period: str,
        snapshot_unique_ids: Iterable[str],
    ) -> List[CRMReviewOppBranchSnapshot]:
        ids = [str(x).strip() for x in (snapshot_unique_ids or []) if x and str(x).strip()]
        owner_ids = [str(x).strip() for x in (owner_crm_user_ids or []) if x and str(x).strip()]
        if not owner_ids or not snapshot_period or not ids:
            return []
        return db_session.exec(
            select(CRMReviewOppBranchSnapshot).where(
                CRMReviewOppBranchSnapshot.owner_id.in_(owner_ids),
                CRMReviewOppBranchSnapshot.snapshot_period == snapshot_period,
                CRMReviewOppBranchSnapshot.unique_id.in_(ids),
            )
        ).all()


crm_review_opp_branch_snapshot_repo = CRMReviewOppBranchSnapshotRepo()


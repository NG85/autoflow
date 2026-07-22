from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import exists, select as sa_select
from sqlmodel import Session

from app.models.crm_data_authority import CrmDataAuthority
from app.repositories.base_repo import BaseRepo


class CrmDataAuthorityRepo(BaseRepo):
    model_cls = CrmDataAuthority

    def _not_deleted_condition(self) -> Any:
        # NULL treated as not deleted
        return (CrmDataAuthority.delete_flag.is_(None)) | (CrmDataAuthority.delete_flag == False)  # noqa: E712

    def has_any_authority(self, db_session: Session, crm_user_id: str, authority_type: str) -> bool:
        """Check whether the CRM user has any authority rows for the given type."""
        stmt = (
            sa_select(1)
            .select_from(CrmDataAuthority)
            .where(CrmDataAuthority.crm_id == str(crm_user_id))
            .where(CrmDataAuthority.type == authority_type)
            .where(self._not_deleted_condition())
            .limit(1)
        )
        return db_session.exec(stmt).first() is not None

    def build_exists_condition(self, crm_user_id: str, authority_type: str, data_id_column) -> Any:
        """Build correlated EXISTS condition against crm_data_authority."""
        return exists(
            sa_select(1)
            .select_from(CrmDataAuthority)
            .where(CrmDataAuthority.crm_id == str(crm_user_id))
            .where(CrmDataAuthority.type == authority_type)
            .where(CrmDataAuthority.data_id == data_id_column)
            .where(self._not_deleted_condition())
        )

    def list_authority_rows(
        self,
        db_session: Session,
        *,
        crm_ids: Sequence[str],
        authority_types: Optional[Iterable[str]] = None,
        max_rows: int = 50000,
    ) -> list[tuple[str, str]]:
        """Materialize ``(type, data_id)`` rows for the given CRM user ids.

        Used by RAG Chat: OAuth data-scope supplies ``crm_ids`` (self + org_scope),
        then IDs are loaded from the mirror for metadata IN filters.
        """
        ids = [str(cid).strip() for cid in crm_ids if str(cid).strip()]
        if not ids or max_rows <= 0:
            return []

        types = [str(t).strip() for t in (authority_types or []) if str(t).strip()]
        stmt = (
            sa_select(CrmDataAuthority.type, CrmDataAuthority.data_id)
            .where(CrmDataAuthority.crm_id.in_(ids))
            .where(self._not_deleted_condition())
        )
        if types:
            stmt = stmt.where(CrmDataAuthority.type.in_(types))
        stmt = stmt.limit(int(max_rows) + 1)

        rows: list[tuple[str, str]] = []
        for data_type, data_id in db_session.exec(stmt):
            if not data_type or not data_id:
                continue
            rows.append((str(data_type), str(data_id)))
        return rows


crm_data_authority_repo = CrmDataAuthorityRepo()



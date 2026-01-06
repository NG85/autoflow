from __future__ import annotations

from typing import Any

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


crm_data_authority_repo = CrmDataAuthorityRepo()



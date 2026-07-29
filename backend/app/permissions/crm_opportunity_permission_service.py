"""商机（crm_opportunity）OAuth 权限：列表功能门控 + data-scope。

商机不继承客户权限；列表经 ``data-scope(entity=crm_opportunity)`` 译 SQL。
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import TextClause, text
from sqlmodel import Session

from app.permissions.crm_opportunity_scope_translator import (
    CRM_OPPORTUNITIES_ID_COLUMN,
    CRM_OPPORTUNITIES_TABLE,
    translate_crm_opportunity_scope_to_sql,
)
from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

CRM_OPPORTUNITY_ENTITY = "crm_opportunity"
CRM_OPPORTUNITY_VIEW_PERMISSION = "crm:opportunity:view"


class CrmOpportunityPermissionService:
    def resolve_crm_user_id(self, session: Session, user_id: UUID) -> Optional[str]:
        return user_profile_repo.get_crm_user_id_by_user_id(session, user_id)

    def gate_view(self, session: Session, user_id: UUID, *, crm_user_id: Optional[str] = None) -> bool:
        """列表功能门控：``crm:opportunity:view``（无 resource）。"""
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=CRM_OPPORTUNITY_VIEW_PERMISSION,
        )
        allowed = bool(check.get("function_allowed"))
        logger.info(
            "crm opportunity gate_view user_id=%s function_allowed=%s",
            user_id,
            allowed,
        )
        return allowed

    def get_data_scope(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        return oauth_client.get_data_scope(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            entity=CRM_OPPORTUNITY_ENTITY,
        )

    def build_list_perm_clause(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
        main_alias: str = CRM_OPPORTUNITIES_TABLE,
        id_column: str = CRM_OPPORTUNITIES_ID_COLUMN,
    ):
        """data-scope(crm_opportunity) → 译 SQL（按商机 unique_id 过滤）。"""
        scope = self.get_data_scope(session, user_id, crm_user_id=crm_user_id)
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        merge = str(scope.get("merge") or "OR")
        return translate_crm_opportunity_scope_to_sql(
            filters,
            merge,
            main_alias=main_alias,
            id_column=id_column,
        )

    def list_perm_where(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
    ) -> TextClause:
        """列表/COUNT/筛选项共用的权限 WHERE 片段。"""
        perm = self.build_list_perm_clause(session, user_id, crm_user_id=crm_user_id)
        clause = text(perm.sql)
        if not perm.params:
            return clause
        bound_params = {key: value for key, value in perm.params.items() if f":{key}" in perm.sql}
        if not bound_params:
            return clause
        return clause.bindparams(**bound_params)


crm_opportunity_permission_service = CrmOpportunityPermissionService()

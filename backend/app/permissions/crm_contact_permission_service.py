"""本地联系人相关 OAuth 权限：单条按所属客户 check + 列表 data-scope。

本地联系人不在 ``crm_data_authority``（无 ``crm_contact`` grant），数据层只校验所属客户
``resource=crm_account``；功能层用 ``crm:contact:*``。
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import TextClause, text
from sqlmodel import Session

from app.permissions.crm_account_scope_translator import (
    LOCAL_CONTACTS_ACCOUNT_COLUMN,
    LOCAL_CONTACTS_TABLE,
    translate_crm_account_scope_to_sql,
)
from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

CRM_ACCOUNT_RESOURCE_TYPE = "crm_account"
CRM_ACCOUNT_ENTITY = "crm_account"
CRM_ACCOUNT_VIEW_PERMISSION = "crm:account:view"
CRM_CONTACT_VIEW_PERMISSION = "crm:contact:view"
CRM_CONTACT_CREATE_PERMISSION = "crm:contact:create"
CRM_CONTACT_EDIT_PERMISSION = "crm:contact:edit"
CRM_CONTACT_DELETE_PERMISSION = "crm:contact:delete"


class CrmContactPermissionService:
    def resolve_crm_user_id(self, session: Session, user_id: UUID) -> Optional[str]:
        return user_profile_repo.get_crm_user_id_by_user_id(session, user_id)

    def gate_view(self, session: Session, user_id: UUID, *, crm_user_id: Optional[str] = None) -> bool:
        """列表功能门控：``crm:contact:view``（无 resource）。"""
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=CRM_CONTACT_VIEW_PERMISSION,
        )
        allowed = bool(check.get("function_allowed"))
        logger.info(
            "crm contact gate_view user_id=%s function_allowed=%s",
            user_id,
            allowed,
        )
        return allowed

    def check_account_access(
        self,
        session: Session,
        user_id: UUID,
        customer_id: str,
        *,
        permission: str = CRM_ACCOUNT_VIEW_PERMISSION,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        """
        POST /permission/check：功能层 + 客户数据层（resource=crm_account）。

        本地联系人无独立 mirror，一律按所属客户鉴权。
        例：查看/编辑/删除用 ``crm:contact:view|edit|delete``；创建用 ``crm:contact:create``。
        """
        account_id = str(customer_id or "").strip()
        if not account_id:
            return False

        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=permission,
            resource={"type": CRM_ACCOUNT_RESOURCE_TYPE, "id": account_id},
        )
        allowed = bool(check.get("allowed"))
        logger.info(
            "local contact oauth account check user_id=%s permission=%s account_id=%s "
            "allowed=%s function_allowed=%s data_allowed=%s",
            user_id,
            permission,
            account_id,
            allowed,
            check.get("function_allowed"),
            check.get("data_allowed"),
        )
        return allowed

    def check_view(
        self,
        session: Session,
        user_id: UUID,
        *,
        customer_id: str,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        """详情查看：``crm:contact:view`` + resource=crm_account（所属客户）。"""
        return self.check_account_access(
            session,
            user_id,
            customer_id,
            permission=CRM_CONTACT_VIEW_PERMISSION,
            crm_user_id=crm_user_id,
        )

    def check_create_on_account(
        self,
        session: Session,
        user_id: UUID,
        customer_id: str,
        *,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        """在指定客户下新建联系人：``crm:contact:create`` + resource crm_account。"""
        return self.check_account_access(
            session,
            user_id,
            customer_id,
            permission=CRM_CONTACT_CREATE_PERMISSION,
            crm_user_id=crm_user_id,
        )

    def get_account_data_scope(
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
            entity=CRM_ACCOUNT_ENTITY,
        )

    def build_list_perm_clause(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
        main_alias: str = LOCAL_CONTACTS_TABLE,
        id_column: str = LOCAL_CONTACTS_ACCOUNT_COLUMN,
    ):
        """data-scope(crm_account) → 译 SQL（按所属客户过滤本地联系人）。"""
        scope = self.get_account_data_scope(session, user_id, crm_user_id=crm_user_id)
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        merge = str(scope.get("merge") or "OR")
        return translate_crm_account_scope_to_sql(
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
        """列表/COUNT 共用的权限 WHERE 片段。"""
        perm = self.build_list_perm_clause(session, user_id, crm_user_id=crm_user_id)
        clause = text(perm.sql)
        if not perm.params:
            return clause
        bound_params = {key: value for key, value in perm.params.items() if f":{key}" in perm.sql}
        if not bound_params:
            return clause
        return clause.bindparams(**bound_params)


crm_contact_permission_service = CrmContactPermissionService()

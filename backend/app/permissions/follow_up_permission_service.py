"""跟进记录（follow_up）OAuth 权限编排：功能门控、列表 data-scope、单条 check。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import TextClause, text
from sqlmodel import Session

from app.permissions.follow_up_context_builder import FollowUpContextBuilder, follow_up_resource_id
from app.permissions.follow_up_scope_translator import ScopeSql, translate_follow_up_scope_to_sql
from app.permissions.user_id_resolver import map_org_scope_from_filters
from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client

if TYPE_CHECKING:
    from app.models.crm_sales_visit_records import CRMSalesVisitRecord

logger = logging.getLogger(__name__)

FOLLOW_UP_ENTITY = "follow_up"
FOLLOW_UP_MAIN_TABLE = "crm_sales_visit_records"
FOLLOW_UP_VIEW_PERMISSION = "sales:follow_up:view"
FOLLOW_UP_EDIT_PERMISSION = "sales:follow_up:edit"
FOLLOW_UP_DELETE_PERMISSION = "sales:follow_up:delete"
FOLLOW_UP_EXPORT_PERMISSION = "sales:follow_up:export"

# OAuth collaborator filter 需 follow_up_collab(user_id) 表支撑。
# crm_sales_visit_records.collaborative_participants 是 CC/通知语义，与 OAuth 协作者不等同，列表暂不接入。


class FollowUpPermissionService:
    def resolve_crm_user_id(self, session: Session, user_id: UUID) -> Optional[str]:
        return user_profile_repo.get_crm_user_id_by_user_id(session, user_id)

    def gate_view(self, session: Session, user_id: UUID, *, crm_user_id: Optional[str] = None) -> bool:
        """列表/菜单功能门控：sales:follow_up:view（无 resource）。"""
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=FOLLOW_UP_VIEW_PERMISSION,
        )
        allowed = bool(check.get("function_allowed"))
        logger.info(
            "follow_up gate_view user_id=%s function_allowed=%s",
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
            entity=FOLLOW_UP_ENTITY,
        )

    def build_list_perm_clause(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
        main_alias: str = FOLLOW_UP_MAIN_TABLE,
        linked_crm_sql: str | None = None,
    ) -> ScopeSql:
        """gate → data-scope → translate filters 为列表 WHERE 片段。"""
        scope = self.get_data_scope(session, user_id, crm_user_id=crm_user_id)
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        merge = str(scope.get("merge") or "OR")
        org_scope_user_ids = map_org_scope_from_filters(session, filters)
        return translate_follow_up_scope_to_sql(
            filters,
            merge,
            main_alias=main_alias,
            user_id=str(user_id),
            org_scope_user_ids=org_scope_user_ids,
            collab_exists_sql=None,
            linked_crm_sql=linked_crm_sql,
        )

    def list_perm_where(self, session: Session, user_id: UUID, *, crm_user_id: Optional[str] = None) -> TextClause:
        """列表/COUNT/导出共用的权限 WHERE 片段（SQLAlchemy text）。"""
        perm = self.build_list_perm_clause(session, user_id, crm_user_id=crm_user_id)
        clause = text(perm.sql)
        if not perm.params:
            return clause
        bound_params = {key: value for key, value in perm.params.items() if f":{key}" in perm.sql}
        if not bound_params:
            return clause
        return clause.bindparams(**bound_params)

    def check_with_context(
        self,
        session: Session,
        user_id: UUID,
        *,
        permission: str,
        resource_type: str,
        resource_id: str,
        context: dict[str, Any],
        crm_user_id: Optional[str] = None,
    ) -> bool:
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=permission,
            resource={"type": resource_type, "id": resource_id},
            context=context,
        )
        return bool(check.get("allowed"))

    def _check_record(
        self,
        session: Session,
        user_id: UUID,
        record: "CRMSalesVisitRecord",
        *,
        permission: str,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        resource_id = follow_up_resource_id(record)
        if not resource_id:
            logger.info("follow_up record check skipped: missing record_id, user_id=%s", user_id)
            return False
        context = FollowUpContextBuilder(session, user_id).build(record)
        return self.check_with_context(
            session,
            user_id,
            permission=permission,
            resource_type=FOLLOW_UP_ENTITY,
            resource_id=resource_id,
            context=context,
            crm_user_id=crm_user_id,
        )

    def check_view(
        self,
        session: Session,
        user_id: UUID,
        record: "CRMSalesVisitRecord",
        *,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        return self._check_record(
            session,
            user_id,
            record,
            permission=FOLLOW_UP_VIEW_PERMISSION,
            crm_user_id=crm_user_id,
        )

    def check_edit(
        self,
        session: Session,
        user_id: UUID,
        record: "CRMSalesVisitRecord",
        *,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        return self._check_record(
            session,
            user_id,
            record,
            permission=FOLLOW_UP_EDIT_PERMISSION,
            crm_user_id=crm_user_id,
        )

    def check_export(self, session: Session, user_id: UUID, *, crm_user_id: Optional[str] = None) -> bool:
        """导出功能层鉴权（列表范围由 data-scope 保证）。"""
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=FOLLOW_UP_EXPORT_PERMISSION,
        )
        return bool(check.get("function_allowed") or check.get("allowed"))

    def batch_row_permissions(
        self,
        session: Session,
        user_id: UUID,
        records: list["CRMSalesVisitRecord"],
        *,
        crm_user_id: Optional[str] = None,
    ) -> dict[str, dict[str, bool]]:
        """
        当前页跟进 batch-check：每条记录 edit + delete，顺序与 OAuth checks 一致。

        Returns:
            record_id -> {"can_edit": bool, "can_delete": bool}
        """
        if not records:
            return {}

        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        builder = FollowUpContextBuilder(session, user_id)
        checks: list[dict[str, Any]] = []
        indexed_resource_ids: list[str] = []

        for record in records:
            resource_id = follow_up_resource_id(record)
            if not resource_id:
                continue
            context = builder.build(record)
            resource = {"type": FOLLOW_UP_ENTITY, "id": resource_id}
            indexed_resource_ids.append(resource_id)
            checks.append(
                {
                    "permission": FOLLOW_UP_EDIT_PERMISSION,
                    "resource": resource,
                    "context": context,
                }
            )
            checks.append(
                {
                    "permission": FOLLOW_UP_DELETE_PERMISSION,
                    "resource": resource,
                    "context": context,
                }
            )

        if not checks:
            return {}

        results = oauth_client.batch_check_permissions(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            checks=checks,
        )

        permissions_by_record_id: dict[str, dict[str, bool]] = {}
        result_index = 0
        for resource_id in indexed_resource_ids:
            edit_result = results[result_index] if result_index < len(results) else {}
            delete_result = results[result_index + 1] if result_index + 1 < len(results) else {}
            permissions_by_record_id[resource_id] = {
                "can_edit": bool(edit_result.get("allowed")),
                "can_delete": bool(delete_result.get("allowed")),
            }
            result_index += 2
        return permissions_by_record_id


follow_up_permission_service = FollowUpPermissionService()

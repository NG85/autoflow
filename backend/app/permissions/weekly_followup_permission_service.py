"""周跟进总结（weekly_followup）OAuth 权限：功能门控 + data-scope 能力探测。"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session

from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

WEEKLY_FOLLOWUP_VIEW_PERMISSION = "sales:weekly_followup:view"
# W3 data-scope 策略 entity_type（与 permission 实体 weekly_followup 对应）
WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY = "sales_weekly_followup"


class WeeklyFollowupPermissionService:
    def resolve_crm_user_id(self, session: Session, user_id: UUID) -> Optional[str]:
        return user_profile_repo.get_crm_user_id_by_user_id(session, user_id)

    def gate_view(self, session: Session, user_id: UUID, *, crm_user_id: Optional[str] = None) -> bool:
        """列表功能门控：``sales:weekly_followup:view``（无 resource）。"""
        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        check = oauth_client.check_permission(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            permission=WEEKLY_FOLLOWUP_VIEW_PERMISSION,
        )
        allowed = bool(check.get("function_allowed"))
        logger.info(
            "weekly_followup gate_view user_id=%s function_allowed=%s",
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
            entity=WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY,
        )

    def has_global_data_scope(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        """data-scope 含 global → 可看 company 周总结列表。"""
        scope = self.get_data_scope(session, user_id, crm_user_id=crm_user_id)
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        for item in filters:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            enabled = item.get("enabled")
            if source == "global" and (enabled is True or str(enabled).lower() == "true"):
                logger.info("weekly_followup global data-scope user_id=%s", user_id)
                return True
        return False


weekly_followup_permission_service = WeeklyFollowupPermissionService()

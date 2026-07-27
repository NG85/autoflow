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
# self 语义 filter（矩阵 SALES=SELF_OWNER；兼容其余自身范围来源）
_SELF_SOURCES = frozenset({"self_only", "self_owner", "self_creator"})


def _is_explicitly_enabled(item: dict[str, Any]) -> bool:
    """global 等过滤器：仅 enabled 明确为 true 时生效（与既有 has_global 口径一致）。"""
    enabled = item.get("enabled")
    return enabled is True or str(enabled).lower() == "true"


def _is_not_disabled(item: dict[str, Any]) -> bool:
    """org_scope 等：缺省视为开启；仅显式 false 时跳过（与 map_org_scope 一致）。"""
    enabled = item.get("enabled")
    if enabled is None:
        return True
    return enabled is True or str(enabled).lower() == "true"


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
        """data-scope 含 global → 可看 company 周总结（列表/详情）。"""
        scope = self.get_data_scope(session, user_id, crm_user_id=crm_user_id)
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        for item in filters:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            if source == "global" and _is_explicitly_enabled(item):
                logger.info("weekly_followup global data-scope user_id=%s", user_id)
                return True
        return False

    def has_team_data_scope(
        self,
        session: Session,
        user_id: UUID,
        *,
        crm_user_id: Optional[str] = None,
    ) -> bool:
        """data-scope 含非 self 过滤器（org_scope / global / biz_support 等）→ 可看团队明细。

        Wave A2：替代遗留 ``report51:dept:view``。矩阵上 SALES_MANAGER / VIRTUAL_TEAM_LEAD
        为 ORG_TEAM_SUB；经营层/管理员为 GLOBAL。仅 self_* 时返回 False（普通销售）。
        """
        scope = self.get_data_scope(session, user_id, crm_user_id=crm_user_id)
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        for item in filters:
            if not isinstance(item, dict):
                continue
            if not _is_not_disabled(item):
                continue
            source = str(item.get("source") or "").strip()
            if not source:
                continue
            if source == "global":
                if not _is_explicitly_enabled(item):
                    continue
                logger.info(
                    "weekly_followup team data-scope user_id=%s source=%s",
                    user_id,
                    source,
                )
                return True
            if source not in _SELF_SOURCES:
                logger.info(
                    "weekly_followup team data-scope user_id=%s source=%s",
                    user_id,
                    source,
                )
                return True
        return False


weekly_followup_permission_service = WeeklyFollowupPermissionService()

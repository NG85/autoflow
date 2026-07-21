"""chats（SIA 问答 / 客户拜访攻略）列表 OAuth data-scope 权限编排。

GET /chats 按 chat_type 映射 OAuth data-scope 实体（会话历史）：
- ChatType.DEFAULT             → enablement_sia_history
- ChatType.CLIENT_VISIT_GUIDE  → enablement_visit_guide_history

其余 chat_type（review_session 等）不在本服务范围，由调用方回退旧逻辑。
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlmodel import Session

from app.models.chat import ChatType
from app.permissions.chat_scope_translator import ChatScopeResult, translate_chat_scope
from app.permissions.user_id_resolver import map_org_scope_from_filters
from app.services.oauth_service import oauth_client

logger = logging.getLogger(__name__)

# chat_type → OAuth data-scope 实体（会话历史）
CHAT_TYPE_SCOPE_ENTITY: dict[ChatType, str] = {
    ChatType.DEFAULT: "enablement_sia_history",
    ChatType.CLIENT_VISIT_GUIDE: "enablement_visit_guide_history",
}


class ChatPermissionService:
    def resolve_entity(self, chat_type: Optional[ChatType | str]) -> Optional[str]:
        """chat_type → data-scope 实体；未纳管/未知类型返回 None。"""
        if chat_type is None:
            return None
        try:
            return CHAT_TYPE_SCOPE_ENTITY.get(ChatType(chat_type))
        except ValueError:
            return None

    def resolve_crm_user_id(self, session: Session, user_id: UUID) -> Optional[str]:
        # 延迟导入避免与 app.repositories 包初始化（repositories/__init__ → chat.py
        # → chat_permission_service）形成循环依赖。
        from app.repositories.user_profile import user_profile_repo

        return user_profile_repo.get_crm_user_id_by_user_id(session, user_id)

    def build_scope(
        self,
        session: Session,
        user_id: UUID,
        chat_type: Optional[ChatType],
        *,
        crm_user_id: Optional[str] = None,
    ) -> ChatScopeResult:
        """data-scope → 译为 chats 可见范围（allow_all / deny / owner_user_ids）。"""
        entity = self.resolve_entity(chat_type)
        if not entity:
            # 未纳管类型：交由调用方回退（不在此拒绝）
            return ChatScopeResult(deny=True)

        resolved_crm_user_id = crm_user_id or self.resolve_crm_user_id(session, user_id)
        scope = oauth_client.get_data_scope(
            user_id=user_id,
            crm_user_id=resolved_crm_user_id,
            entity=entity,
        )
        filters = scope.get("filters") if isinstance(scope.get("filters"), list) else []
        org_scope_user_ids = map_org_scope_from_filters(session, filters)
        result = translate_chat_scope(
            filters,
            user_id=str(user_id),
            org_scope_user_ids=org_scope_user_ids,
        )
        logger.info(
            "chat scope user_id=%s chat_type=%s entity=%s allow_all=%s deny=%s owners=%d",
            user_id,
            chat_type,
            entity,
            result.allow_all,
            result.deny,
            len(result.owner_user_ids),
        )
        return result


chat_permission_service = ChatPermissionService()

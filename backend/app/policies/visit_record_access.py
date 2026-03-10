from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, List
from uuid import UUID

from sqlmodel import Session

from app.repositories.user_profile import user_profile_repo
from app.services.oauth_service import oauth_client


RolesAndPermissionsProvider = Callable[[UUID], Dict[str, Any]]
IsAdminUserFn = Callable[[UUID, Session, Optional[List[str]]], bool]


@dataclass
class VisitRecordAccessPolicy:
    """
    拜访记录访问策略：仅按汇报关系控制（与通知对称）。
    - 本人：可查看自己的拜访记录。
    - 汇报下属：可查看所有汇报给自己的下属的拜访记录（OAuth 下属链）。
    """

    session: Session
    current_user_id: Optional[UUID]
    roles_and_permissions_provider: RolesAndPermissionsProvider
    is_admin_user_fn: IsAdminUserFn

    _permissions: Optional[List[str]] = None
    _is_admin: Optional[bool] = None
    _my_oauth_user_id: Optional[str] = None
    _my_subordinate_user_ids: Optional[List[UUID]] = None

    @property
    def permissions(self) -> List[str]:
        if self._permissions is not None:
            return self._permissions
        if not self.current_user_id:
            self._permissions = []
            return self._permissions
        roles_and_permissions = self.roles_and_permissions_provider(self.current_user_id)
        self._permissions = roles_and_permissions.get("permissions", []) if isinstance(roles_and_permissions, dict) else []
        return self._permissions

    @property
    def is_admin(self) -> bool:
        if self._is_admin is not None:
            return self._is_admin
        if not self.current_user_id:
            self._is_admin = False
            return self._is_admin
        self._is_admin = bool(self.is_admin_user_fn(self.current_user_id, self.session, self.permissions))
        return self._is_admin

    @property
    def my_oauth_user_id(self) -> Optional[str]:
        if self._my_oauth_user_id is not None:
            return self._my_oauth_user_id
        if not self.current_user_id:
            self._my_oauth_user_id = None
            return None
        profile = user_profile_repo.get_by_user_id(self.session, self.current_user_id)
        self._my_oauth_user_id = (profile.oauth_user_id if profile else None) or None
        return self._my_oauth_user_id

    @property
    def my_subordinate_user_ids(self) -> List[UUID]:
        """汇报给当前用户的所有下属（OAuth 下属链），用于「可查看下属的拜访记录」."""
        if self._my_subordinate_user_ids is not None:
            return self._my_subordinate_user_ids
        if not self.current_user_id:
            self._my_subordinate_user_ids = []
            return self._my_subordinate_user_ids
        try:
            result = oauth_client.get_subordinate_chain(
                user_id=self.current_user_id,
                include_subordinate_identity=True,
            )
            subordinates = (result or {}).get("subordinates") or []
            ids: List[UUID] = []
            for item in subordinates:
                if not isinstance(item, dict):
                    continue
                uid = item.get("user_id") or item.get("userId")
                if not uid:
                    continue
                try:
                    ids.append(UUID(str(uid)))
                except (ValueError, TypeError):
                    continue
            self._my_subordinate_user_ids = ids
        except Exception:
            self._my_subordinate_user_ids = []
        return self._my_subordinate_user_ids

    def can_access_single_recorder(self, recorder_id: Optional[UUID]) -> bool:
        """单条判断：当前用户是否可查看该记录人的拜访记录（本人或汇报下属）。"""
        if not self.current_user_id:
            # backward compatible: no user provided -> allow
            return True
        if not recorder_id:
            return False
        if self.is_admin:
            return True
        if self.current_user_id == recorder_id:
            return True
        return recorder_id in self.my_subordinate_user_ids

    def list_access_predicate(self, record_model) -> Optional[Any]:
        """
        Returns a SQLAlchemy predicate for list queries, or None if no filtering needed.

        record_model should be the mapped class (e.g. CRMSalesVisitRecord).
        """
        if not self.current_user_id:
            return None
        if self.is_admin:
            return None

        predicate = (record_model.recorder_id == self.current_user_id)

        # 汇报下属：可查看所有下属的拜访记录（OAuth 下属链）
        subordinate_ids = self.my_subordinate_user_ids
        if subordinate_ids:
            predicate = predicate | (record_model.recorder_id.in_(subordinate_ids))

        return predicate



from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, List
from uuid import UUID

from sqlmodel import Session, select, func, String
from sqlalchemy import and_, exists

from app.models.user_profile import UserProfile
from app.models.user_department_relation import UserDepartmentRelation
from app.repositories.user_profile import user_profile_repo
from app.repositories.user_department_relation import user_department_relation_repo


RolesAndPermissionsProvider = Callable[[UUID], Dict[str, Any]]
IsAdminUserFn = Callable[[UUID, Session, Optional[List[str]]], bool]


@dataclass
class VisitRecordAccessPolicy:
    """
    Visit record access policy.

    Owns the authorization rules, separate from query construction.
    - Primary rule: user_department_relation (dept view / leader).
    - Fallback rule: profiles direct manager relationship (virtual reporting line).
    """

    session: Session
    current_user_id: Optional[UUID]
    roles_and_permissions_provider: RolesAndPermissionsProvider
    is_admin_user_fn: IsAdminUserFn

    _permissions: Optional[List[str]] = None
    _is_admin: Optional[bool] = None
    _is_leader: Optional[bool] = None
    _my_department_id: Optional[str] = None
    _my_oauth_user_id: Optional[str] = None

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
    def has_dept_view(self) -> bool:
        return "report51:dept:view" in (self.permissions or [])

    @property
    def is_leader(self) -> bool:
        if self._is_leader is not None:
            return self._is_leader
        if not self.current_user_id:
            self._is_leader = False
            return self._is_leader
        self._is_leader = bool(
            user_department_relation_repo.get_is_leader_by_user_ids(
                self.session,
                [str(self.current_user_id)],
            ).get(str(self.current_user_id), False)
        )
        return self._is_leader

    @property
    def my_department_id(self) -> Optional[str]:
        if self._my_department_id is not None:
            return self._my_department_id
        if not self.current_user_id:
            self._my_department_id = None
            return None
        if not (self.has_dept_view or self.is_leader):
            self._my_department_id = None
            return None
        self._my_department_id = user_department_relation_repo.get_primary_department_by_user_ids(
            self.session,
            [str(self.current_user_id)],
        ).get(str(self.current_user_id))
        return self._my_department_id

    def can_access_single_recorder(self, recorder_id: Optional[UUID]) -> bool:
        """Single-record check to avoid building large IN lists."""
        if not self.current_user_id:
            # backward compatible: no user provided -> allow
            return True
        if not recorder_id:
            return False
        if self.is_admin:
            return True
        if self.current_user_id == recorder_id:
            return True

        # Fallback: direct manager can view direct subordinate records
        my_oid = self.my_oauth_user_id
        if my_oid:
            target = self.session.exec(select(UserProfile).where(UserProfile.user_id == recorder_id)).first()
            if target and target.direct_manager_id and str(target.direct_manager_id).strip() == str(my_oid).strip():
                return True

        # Dept rule (only if leader/dept:view)
        dept_id = self.my_department_id
        if not dept_id:
            return False
        dept_map = user_department_relation_repo.get_primary_department_by_user_ids(
            self.session,
            [str(self.current_user_id), str(recorder_id)],
        )
        return bool(dept_map.get(str(self.current_user_id)) and dept_map.get(str(self.current_user_id)) == dept_map.get(str(recorder_id)))

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

        # Dept visibility: match department_id via EXISTS on user_department_relation
        dept_id = self.my_department_id
        if dept_id:
            # recorder_id is GUID stored as 32-hex; user_id is CHAR(36).
            recorder_id_expr = func.cast(record_model.recorder_id, String)
            recorder_id_uuid36 = func.concat(
                func.substr(recorder_id_expr, 1, 8), "-",
                func.substr(recorder_id_expr, 9, 4), "-",
                func.substr(recorder_id_expr, 13, 4), "-",
                func.substr(recorder_id_expr, 17, 4), "-",
                func.substr(recorder_id_expr, 21, 12),
            )
            predicate = predicate | exists(
                select(1).where(
                    and_(
                        UserDepartmentRelation.department_id == dept_id,
                        UserDepartmentRelation.is_active == True,
                        UserDepartmentRelation.user_id == recorder_id_uuid36,
                    )
                )
            )

        # Fallback: direct manager -> direct subordinate
        my_oid = self.my_oauth_user_id
        if my_oid:
            predicate = predicate | exists(
                select(1).where(
                    and_(
                        UserProfile.user_id == record_model.recorder_id,
                        UserProfile.direct_manager_id == my_oid,
                        UserProfile.is_active == True,
                    )
                )
            )

        return predicate



"""从跟进记录行组装 OAuth /permission/check context。"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlmodel import Session

from app.models.crm_sales_visit_records import CRMSalesVisitRecord
from app.services.oauth_service import oauth_client

_MANAGER_ROLE_CODES = frozenset({"sales_manager", "team_leader", "area_leader"})


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compact_user_id(value: Any) -> Optional[str]:
    text = _optional_str(value)
    return text.replace("-", "") if text else None


def _resolve_subordinate_user_ids(current_user_id: UUID) -> list[UUID]:
    try:
        result = oauth_client.get_subordinate_chain(
            user_id=current_user_id,
            include_subordinate_identity=True,
        )
    except Exception:
        return []

    subordinates = (result or {}).get("subordinates") or []
    ids: list[UUID] = []
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
    return ids


def _user_has_manager_role(user_id: UUID) -> bool:
    roles_and_permissions = oauth_client.query_user_roles_and_permissions(user_id=user_id)
    roles = roles_and_permissions.get("roles", []) if isinstance(roles_and_permissions, dict) else []
    if not isinstance(roles, list):
        return False
    for role in roles:
        if not isinstance(role, dict):
            continue
        code = str(role.get("code") or "").strip().lower()
        if code in _MANAGER_ROLE_CODES:
            return True
    return False


class FollowUpContextBuilder:
    def __init__(self, session: Session, current_user_id: UUID) -> None:
        self.session = session
        self.current_user_id = current_user_id
        self._subordinate_user_ids: Optional[list[UUID]] = None

    @property
    def subordinate_user_ids(self) -> list[UUID]:
        if self._subordinate_user_ids is None:
            self._subordinate_user_ids = _resolve_subordinate_user_ids(self.current_user_id)
        return self._subordinate_user_ids

    def build(self, record: CRMSalesVisitRecord) -> dict[str, Any]:
        recorder_id = getattr(record, "recorder_id", None)
        recorder_id_str = _compact_user_id(recorder_id)
        subordinate_ids = self.subordinate_user_ids
        is_subordinate_creator = bool(
            recorder_id
            and recorder_id != self.current_user_id
            and recorder_id in subordinate_ids
        )
        is_manager = bool(subordinate_ids) or _user_has_manager_role(self.current_user_id)

        return {
            "recorder_id": recorder_id_str,
            # collaborative_participants ≠ OAuth 协作者；固定 false
            "is_collaborator": False,
            "account_id": _optional_str(getattr(record, "account_id", None)),
            "opportunity_id": _optional_str(getattr(record, "opportunity_id", None)),
            "partner_id": _optional_str(getattr(record, "partner_id", None)),
            "is_manager": is_manager,
            "is_subordinate_creator": is_subordinate_creator,
        }


def follow_up_resource_id(record: CRMSalesVisitRecord) -> Optional[str]:
    return _optional_str(getattr(record, "record_id", None))

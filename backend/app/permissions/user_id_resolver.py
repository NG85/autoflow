"""将 OAuth data-scope 中的 crmUserIds 映射为 users.id。"""

from __future__ import annotations

import logging
from typing import Any, Iterable
from uuid import UUID

from sqlmodel import Session, select

from app.models.user_profile import UserProfile

logger = logging.getLogger(__name__)

ORG_SCOPE_TEAM_SUBORDINATES = "team_subordinates"


def _org_scope_mode(item: dict[str, Any]) -> str:
    return str(item.get("mode") or "").strip().lower()


def _include_self(item: dict[str, Any]) -> bool:
    for key in ("includeSelf", "include_self"):
        if key in item:
            return bool(item[key])
    return False


def _crm_user_ids_from_item(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for key in ("crmUserIds", "crm_user_ids"):
        value = item.get(key)
        if not isinstance(value, list):
            continue
        for raw in value:
            crm_id = str(raw or "").strip()
            if crm_id and crm_id not in seen:
                seen.add(crm_id)
                ids.append(crm_id)
    return ids


def _crm_user_ids_from_filters(filters: Iterable[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in filters or []:
        if (item.get("source") or "").strip() != "org_scope":
            continue
        for crm_id in _crm_user_ids_from_item(item):
            if crm_id not in seen:
                seen.add(crm_id)
                ids.append(crm_id)
    return ids


def _subordinate_user_ids_from_oauth(user_id: UUID) -> list[str]:
    from app.services.oauth_service import oauth_client

    try:
        result = oauth_client.get_subordinate_chain(
            user_id=user_id,
            include_subordinate_identity=True,
        )
    except Exception:
        logger.exception("OAuth subordinate-chain failed for org_scope expansion, user_id=%s", user_id)
        return []

    ids: list[str] = []
    for item in (result or {}).get("subordinates") or []:
        if not isinstance(item, dict):
            continue
        uid = item.get("user_id") or item.get("userId")
        if not uid:
            continue
        uid_str = str(uid)
        if uid_str not in ids:
            ids.append(uid_str)
    return ids


def map_crm_user_ids_to_user_ids(session: Session, crm_user_ids: list[str]) -> list[str]:
    """crm_user_id → users.id（字符串 UUID）。"""
    unique_ids = list(dict.fromkeys(str(crm_id).strip() for crm_id in crm_user_ids if str(crm_id).strip()))
    if not unique_ids:
        return []

    rows = session.exec(
        select(UserProfile.user_id).where(
            UserProfile.crm_user_id.in_(unique_ids),
            UserProfile.is_active == True,  # noqa: E712
            UserProfile.user_id.is_not(None),
        )
    ).all()

    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        user_id = row if isinstance(row, UUID) else getattr(row, "user_id", None)
        if not user_id:
            continue
        user_id_str = str(user_id)
        if user_id_str not in seen:
            seen.add(user_id_str)
            result.append(user_id_str)
    return result


def _resolve_team_subordinates_user_ids(
    session: Session,
    item: dict[str, Any],
) -> list[str]:
    """org_scope mode=team_subordinates：锚点 crm_user_ids + OAuth 下属链展开为 users.id。"""
    anchor_user_ids = map_crm_user_ids_to_user_ids(session, _crm_user_ids_from_item(item))
    include_self = _include_self(item)
    result: list[str] = []
    seen: set[str] = set()

    for anchor_str in anchor_user_ids:
        try:
            anchor_uuid = UUID(anchor_str)
        except (ValueError, TypeError):
            logger.warning("org_scope team_subordinates skipped invalid anchor user_id=%s", anchor_str)
            continue

        if include_self and anchor_str not in seen:
            seen.add(anchor_str)
            result.append(anchor_str)

        for sub_id in _subordinate_user_ids_from_oauth(anchor_uuid):
            if not include_self and sub_id == anchor_str:
                continue
            if sub_id not in seen:
                seen.add(sub_id)
                result.append(sub_id)

    return result


def map_org_scope_from_filters(session: Session, filters: list[dict[str, Any]] | None) -> list[str]:
    """从 data-scope filters 解析 org_scope，映射/展开为 users.id 列表。"""
    result: list[str] = []
    seen: set[str] = set()

    for item in filters or []:
        if (item.get("source") or "").strip() != "org_scope":
            continue

        mode = _org_scope_mode(item)
        if mode == ORG_SCOPE_TEAM_SUBORDINATES:
            user_ids = _resolve_team_subordinates_user_ids(session, item)
        else:
            user_ids = map_crm_user_ids_to_user_ids(session, _crm_user_ids_from_item(item))

        for user_id in user_ids:
            if user_id not in seen:
                seen.add(user_id)
                result.append(user_id)

    return result

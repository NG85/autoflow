"""拜访记录卡片抄送规则解析。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import Session

from app.models.notification_cc_rule import EVENT_TYPE_VISIT_RECORD_CARD
from app.platforms.constants import PLATFORM_DINGTALK, PLATFORM_FEISHU, PLATFORM_LARK
from app.repositories.notification_cc_rule import notification_cc_rule_repo
from app.repositories.user_profile import user_profile_repo

logger = logging.getLogger(__name__)

CC_SCOPE_GLOBAL = "global"

SUPPORTED_PLATFORMS = frozenset({PLATFORM_FEISHU, PLATFORM_LARK, PLATFORM_DINGTALK})


def _profile_to_recipient(
    profile,
    *,
    recipient_type: str,
    cc_scope: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not profile or not getattr(profile, "is_active", True):
        return None
    oauth_account = profile.oauth_user
    if not oauth_account:
        return None
    platform = oauth_account.provider
    open_id = oauth_account.open_id
    if platform not in SUPPORTED_PLATFORMS or not open_id:
        return None
    recipient = {
        "open_id": open_id,
        "name": profile.name or "Unknown",
        "type": recipient_type,
        "department": profile.department or "公司",
        "receive_id_type": "open_id",
        "platform": platform,
    }
    if cc_scope:
        recipient["cc_scope"] = cc_scope
    return recipient


def _merge_recipients_into_platform_map(
    recipients_by_platform: Dict[str, List[Dict[str, Any]]],
    recipients: List[Dict[str, Any]],
) -> None:
    for recipient in recipients:
        platform = recipient.get("platform")
        if not platform:
            continue
        if platform not in recipients_by_platform:
            recipients_by_platform[platform] = []
        existing_open_ids = {r["open_id"] for r in recipients_by_platform[platform]}
        open_id = recipient.get("open_id")
        if not open_id or open_id in existing_open_ids:
            continue
        recipients_by_platform[platform].append(recipient)


def resolve_visit_record_cc_recipients(
    db_session: Session,
    *,
    recorder_user_id: Optional[UUID],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    解析拜访记录卡片抄送接收者：仅来自 notification_cc_rules（含 scope_type=global）。

    算法见 backend/docs/visit-record-cc-rules.md。
    """
    recipients_by_platform: Dict[str, List[Dict[str, Any]]] = {}

    if not recorder_user_id:
        return recipients_by_platform

    rules = notification_cc_rule_repo.list_enabled_rules_for_recorder(
        db_session,
        event_type=EVENT_TYPE_VISIT_RECORD_CARD,
        recorder_user_id=recorder_user_id,
    )
    recipient_scopes = notification_cc_rule_repo.merge_recipient_scopes(rules)
    if rules:
        logger.info(
            "Visit record CC rules matched: recorder_user_id=%s, rule_ids=%s, recipient_scopes=%s",
            recorder_user_id,
            [rule.id for rule in rules],
            [(str(uid), scope) for uid, scope in recipient_scopes],
        )

    recipient_user_ids = [uid for uid, _ in recipient_scopes]
    scope_by_user_id = dict(recipient_scopes)
    profiles = user_profile_repo.get_by_user_ids(db_session, recipient_user_ids)
    profile_by_user_id = {profile.user_id: profile for profile in profiles if profile.user_id}

    configured_recipients: List[Dict[str, Any]] = []
    for user_id in recipient_user_ids:
        cc_scope = scope_by_user_id.get(user_id, CC_SCOPE_GLOBAL)
        profile = profile_by_user_id.get(user_id)
        if not profile:
            logger.warning("Visit record CC recipient profile not found: user_id=%s", user_id)
            continue
        recipient = _profile_to_recipient(
            profile,
            recipient_type="configured_cc",
            cc_scope=cc_scope,
        )
        if not recipient:
            logger.warning(
                "Visit record CC recipient missing platform/open_id: user_id=%s, name=%s",
                user_id,
                profile.name,
            )
            continue
        configured_recipients.append(recipient)

    _merge_recipients_into_platform_map(recipients_by_platform, configured_recipients)
    return recipients_by_platform

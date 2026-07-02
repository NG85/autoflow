"""拜访卡片推送：接收者构建与 active profile 过滤辅助。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def log_skip_inactive_visit_recipient(
    recipient: Dict[str, Any],
    *,
    platform: Optional[str] = None,
    recipient_type: Optional[str] = None,
) -> None:
    """记录因 user profile 非 active 或缺失而跳过的接收者。"""
    logger.info(
        "Skip visit record push to inactive/missing profile: name=%s type=%s open_id=%s platform=%s",
        recipient.get("name"),
        recipient_type or recipient.get("type"),
        recipient.get("open_id"),
        platform or recipient.get("platform"),
    )


def filter_recipient_list_by_active_open_ids(
    recipients: List[Dict[str, Any]],
    active_open_ids: set[str],
    *,
    platform: Optional[str] = None,
    recipient_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """仅保留 open_id 对应 is_active profile 的接收者。"""
    kept: List[Dict[str, Any]] = []
    for recipient in recipients:
        open_id = recipient.get("open_id")
        if not open_id:
            continue
        if str(open_id) in active_open_ids:
            kept.append(recipient)
            continue
        log_skip_inactive_visit_recipient(
            recipient,
            platform=platform or recipient.get("platform"),
            recipient_type=recipient_type,
        )
    return kept

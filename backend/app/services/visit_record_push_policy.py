"""拜访记录推送策略：按接收角色配置是否推送、以及推哪一种卡片。

不同环境可以配不同组合，例如：
- 销售轻量复盘卡、汇报链/抄送走原模板卡
- 全员轻量卡
- 只推销售、关掉上级/群

未配置或无法解析时，全部角色保持原模板卡（legacy），与历史行为一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.site_settings import SiteSetting

CARD_LEGACY = "legacy"
CARD_RECAP_LITE = "recap_lite"
VALID_CARDS = frozenset({CARD_LEGACY, CARD_RECAP_LITE})

RECIPIENT_TYPES = (
    "recorder",
    "leader",
    "configured_cc",
    "collaborative_participant",
)
GROUP_TYPES = ("review", "brief")

_FALSE_TOKENS = frozenset({"false", "off", "disabled", "none", "0", "no"})
_TRUE_TOKENS = frozenset({"true", "on", "enabled", "1", "yes"})
_DEFAULT_RECAP_QUERY = "panel=recap"


@dataclass(frozen=True)
class RolePushSpec:
    enabled: bool
    card: str


@dataclass(frozen=True)
class VisitRecordPushPolicy:
    recipients: Dict[str, RolePushSpec]
    groups: Dict[str, RolePushSpec]
    recap_detail_query: str = _DEFAULT_RECAP_QUERY

    def recipient_spec(self, role: Optional[str]) -> RolePushSpec:
        if role and role in self.recipients:
            return self.recipients[role]
        return RolePushSpec(enabled=True, card=CARD_LEGACY)

    def recipient_enabled(self, role: Optional[str]) -> bool:
        return self.recipient_spec(role).enabled

    def recipient_card(self, role: Optional[str]) -> str:
        spec = self.recipient_spec(role)
        if not spec.enabled:
            return CARD_LEGACY
        return spec.card if spec.card in VALID_CARDS else CARD_LEGACY

    def group_spec(self, role: Optional[str]) -> RolePushSpec:
        if role and role in self.groups:
            return self.groups[role]
        return RolePushSpec(enabled=True, card=CARD_LEGACY)

    def group_enabled(self, role: Optional[str]) -> bool:
        return self.group_spec(role).enabled

    def group_card(self, role: Optional[str]) -> str:
        spec = self.group_spec(role)
        if not spec.enabled:
            return CARD_LEGACY
        return spec.card if spec.card in VALID_CARDS else CARD_LEGACY

    def uses_recap_lite(self) -> bool:
        return any(
            spec.enabled and spec.card == CARD_RECAP_LITE
            for spec in (*self.recipients.values(), *self.groups.values())
        )


def _all_legacy_policy() -> VisitRecordPushPolicy:
    legacy = RolePushSpec(enabled=True, card=CARD_LEGACY)
    return VisitRecordPushPolicy(
        recipients={role: legacy for role in RECIPIENT_TYPES},
        groups={role: legacy for role in GROUP_TYPES},
        recap_detail_query=_DEFAULT_RECAP_QUERY,
    )


DEFAULT_VISIT_RECORD_PUSH_POLICY = _all_legacy_policy()


def _coerce_bool(raw: Any, default: bool = True) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(raw)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in _FALSE_TOKENS:
            return False
        if token in _TRUE_TOKENS:
            return True
    return default


def _coerce_card(raw: Any, default: str) -> str:
    if raw is None:
        return default
    card = str(raw).strip().lower()
    if card in VALID_CARDS:
        return card
    return default


def _normalize_role_spec(raw: Any, default_card: str) -> RolePushSpec:
    """把 bool / 卡片名 / {enabled, card} 统一成 RolePushSpec。"""
    if raw is None:
        return RolePushSpec(enabled=True, card=default_card)
    if isinstance(raw, bool):
        return RolePushSpec(enabled=raw, card=default_card)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return RolePushSpec(enabled=bool(raw), card=default_card)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in _FALSE_TOKENS:
            return RolePushSpec(enabled=False, card=default_card)
        if token in _TRUE_TOKENS:
            return RolePushSpec(enabled=True, card=default_card)
        if token in VALID_CARDS:
            return RolePushSpec(enabled=True, card=token)
        return RolePushSpec(enabled=True, card=default_card)
    if isinstance(raw, Mapping):
        enabled = _coerce_bool(raw.get("enabled", True), True)
        card_raw = raw.get("card")
        if card_raw is None and isinstance(raw.get("strategy"), str):
            card_raw = raw.get("strategy")
        return RolePushSpec(enabled=enabled, card=_coerce_card(card_raw, default_card))
    return RolePushSpec(enabled=True, card=default_card)


def parse_visit_record_push_policy(raw: Any) -> VisitRecordPushPolicy:
    """解析 SiteSetting。无法识别的输入回退为全 legacy。"""
    if not isinstance(raw, Mapping):
        return DEFAULT_VISIT_RECORD_PUSH_POLICY

    default_card = _coerce_card(raw.get("strategy") or raw.get("card"), CARD_LEGACY)
    recipient_raw = raw.get("recipients")
    if not isinstance(recipient_raw, Mapping):
        recipient_raw = raw
    group_raw = raw.get("groups")
    if not isinstance(group_raw, Mapping):
        group_raw = {}

    recipients = {
        role: _normalize_role_spec(recipient_raw.get(role), default_card)
        for role in RECIPIENT_TYPES
    }
    groups = {
        role: _normalize_role_spec(group_raw.get(role), default_card)
        for role in GROUP_TYPES
    }

    query = raw.get("recap_detail_query")
    if isinstance(query, str) and query.strip():
        recap_query = query.strip().lstrip("?")
    else:
        recap_query = _DEFAULT_RECAP_QUERY

    return VisitRecordPushPolicy(
        recipients=recipients,
        groups=groups,
        recap_detail_query=recap_query,
    )


def load_visit_record_push_policy() -> VisitRecordPushPolicy:
    try:
        raw = SiteSetting.get_setting("visit_record_push_policy")
    except Exception:
        return DEFAULT_VISIT_RECORD_PUSH_POLICY
    return parse_visit_record_push_policy(raw)


def filter_recipients_by_policy(
    recipients_by_platform: Mapping[str, Iterable[Dict[str, Any]]],
    policy: VisitRecordPushPolicy,
) -> Dict[str, List[Dict[str, Any]]]:
    """去掉策略里关闭的个人接收角色；未知 type 仍保留（按 legacy 处理）。"""
    filtered: Dict[str, List[Dict[str, Any]]] = {}
    for platform, recipients in recipients_by_platform.items():
        kept = [
            recipient
            for recipient in recipients
            if policy.recipient_enabled(recipient.get("type"))
        ]
        if kept:
            filtered[str(platform)] = kept
    return filtered

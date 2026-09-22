"""日/周报与今日重点推送策略：按槽位开关变体。

未配置或无法解析时，各报告槽位仅 kpi_card 开启，与历史「只发统计卡」一致。
今日重点：sales_daily 仍是日报变体；company_highlights / department_highlights 为独立槽位。
visit_report 仅周报槽位。summary_md 仅日报槽位（公司/部门）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from app.services.notification_scene_catalog import (
    HIGHLIGHTS_SLOTS,
    POLICY_SLOTS,
    REPORT_SLOT_ALIASES,
    REPORT_SLOTS,
    SUMMARY_MD_SLOTS,
    TODAY_HIGHLIGHTS_SLOTS,
    VARIANT_KPI_CARD,
    VARIANT_SUMMARY_MD,
    VARIANT_TODAY_HIGHLIGHTS,
    VARIANT_VISIT_REPORT,
    normalize_scene,
)
from app.site_settings import SiteSetting

_FALSE_TOKENS = frozenset({"false", "off", "disabled", "none", "0", "no"})
_TRUE_TOKENS = frozenset({"true", "on", "enabled", "1", "yes"})

REPORT_VARIANTS = (
    VARIANT_KPI_CARD,
    VARIANT_VISIT_REPORT,
    VARIANT_TODAY_HIGHLIGHTS,
    VARIANT_SUMMARY_MD,
)
HIGHLIGHTS_VARIANTS = (VARIANT_TODAY_HIGHLIGHTS,)
VISIT_REPORT_SLOTS = frozenset({"company_weekly", "department_weekly"})
TODAY_HIGHLIGHTS_SLOT_SET = frozenset(TODAY_HIGHLIGHTS_SLOTS)
SUMMARY_MD_SLOT_SET = frozenset(SUMMARY_MD_SLOTS)
HIGHLIGHTS_SLOT_SET = frozenset(HIGHLIGHTS_SLOTS)


@dataclass(frozen=True)
class VariantSpec:
    enabled: bool
    recipient_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportPushPolicy:
    slots: Dict[str, Dict[str, VariantSpec]]

    def variant_spec(self, slot: str, variant: str) -> VariantSpec:
        slot_key = normalize_scene(slot)
        slot_map = self.slots.get(slot_key) or {}
        spec = slot_map.get(variant)
        if spec is not None:
            return spec
        if variant == VARIANT_KPI_CARD and slot_key in REPORT_SLOTS:
            return VariantSpec(enabled=True)
        return VariantSpec(enabled=False)

    def variant_enabled(self, slot: str, variant: str) -> bool:
        slot_key = normalize_scene(slot)
        if variant == VARIANT_VISIT_REPORT and slot_key not in VISIT_REPORT_SLOTS:
            return False
        if variant == VARIANT_TODAY_HIGHLIGHTS and slot_key not in TODAY_HIGHLIGHTS_SLOT_SET:
            return False
        if variant == VARIANT_SUMMARY_MD and slot_key not in SUMMARY_MD_SLOT_SET:
            return False
        if variant == VARIANT_KPI_CARD and slot_key in HIGHLIGHTS_SLOT_SET:
            return False
        return self.variant_spec(slot, variant).enabled

    def override_user_ids(self, slot: str, variant: str) -> List[str]:
        return list(self.variant_spec(slot, variant).recipient_user_ids)


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


def _normalize_user_ids(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        uid = raw.strip()
        return (uid,) if uid else ()
    if isinstance(raw, (list, tuple)):
        seen: set[str] = set()
        out: List[str] = []
        for item in raw:
            uid = str(item or "").strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            out.append(uid)
        return tuple(out)
    return ()


def _normalize_variant_spec(raw: Any, *, default_enabled: bool) -> VariantSpec:
    if raw is None:
        return VariantSpec(enabled=default_enabled)
    if isinstance(raw, bool):
        return VariantSpec(enabled=raw)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return VariantSpec(enabled=bool(raw))
    if isinstance(raw, str):
        return VariantSpec(enabled=_coerce_bool(raw, default_enabled))
    if isinstance(raw, Mapping):
        enabled = _coerce_bool(raw.get("enabled", default_enabled), default_enabled)
        ids = _normalize_user_ids(
            raw.get("recipient_user_ids") or raw.get("recipients")
        )
        return VariantSpec(enabled=enabled, recipient_user_ids=ids)
    return VariantSpec(enabled=default_enabled)


def _slot_variants_list(slot: str) -> tuple[str, ...]:
    if slot in HIGHLIGHTS_SLOT_SET:
        return HIGHLIGHTS_VARIANTS
    return REPORT_VARIANTS


def _default_slot_variants(slot: str) -> Dict[str, VariantSpec]:
    if slot in HIGHLIGHTS_SLOT_SET:
        return {VARIANT_TODAY_HIGHLIGHTS: VariantSpec(enabled=False)}
    variants: Dict[str, VariantSpec] = {
        VARIANT_KPI_CARD: VariantSpec(enabled=True),
        VARIANT_VISIT_REPORT: VariantSpec(enabled=False),
        VARIANT_TODAY_HIGHLIGHTS: VariantSpec(enabled=False),
        VARIANT_SUMMARY_MD: VariantSpec(enabled=False),
    }
    if slot not in TODAY_HIGHLIGHTS_SLOT_SET:
        variants[VARIANT_TODAY_HIGHLIGHTS] = VariantSpec(enabled=False)
    if slot not in VISIT_REPORT_SLOTS:
        variants[VARIANT_VISIT_REPORT] = VariantSpec(enabled=False)
    if slot not in SUMMARY_MD_SLOT_SET:
        variants[VARIANT_SUMMARY_MD] = VariantSpec(enabled=False)
    return variants


def default_report_push_policy() -> ReportPushPolicy:
    return ReportPushPolicy(
        slots={slot: _default_slot_variants(slot) for slot in POLICY_SLOTS}
    )


DEFAULT_REPORT_PUSH_POLICY = default_report_push_policy()


def _slot_raw_from_mapping(raw: Mapping[str, Any], slot: str) -> Any:
    slot_raw = raw.get(slot)
    if slot_raw is not None:
        return slot_raw
    slot_raw = raw.get(f"{slot}_report")
    if slot_raw is not None:
        return slot_raw
    for alias, canonical in REPORT_SLOT_ALIASES.items():
        if canonical == slot:
            slot_raw = raw.get(alias)
            if slot_raw is not None:
                return slot_raw
    return None


def parse_report_push_policy(raw: Any) -> ReportPushPolicy:
    if not isinstance(raw, Mapping):
        return DEFAULT_REPORT_PUSH_POLICY

    slots: Dict[str, Dict[str, VariantSpec]] = {}
    for slot in POLICY_SLOTS:
        slot_raw = _slot_raw_from_mapping(raw, slot)
        variants = _default_slot_variants(slot)
        allowed = _slot_variants_list(slot)
        if isinstance(slot_raw, Mapping):
            for variant in allowed:
                default_enabled = variant == VARIANT_KPI_CARD and slot not in HIGHLIGHTS_SLOT_SET
                if variant in slot_raw:
                    variants[variant] = _normalize_variant_spec(
                        slot_raw.get(variant), default_enabled=default_enabled
                    )
                elif (
                    variant == VARIANT_KPI_CARD
                    and slot not in HIGHLIGHTS_SLOT_SET
                    and "enabled" in slot_raw
                ):
                    variants[variant] = _normalize_variant_spec(
                        slot_raw, default_enabled=True
                    )
                elif (
                    variant == VARIANT_TODAY_HIGHLIGHTS
                    and slot in HIGHLIGHTS_SLOT_SET
                    and "enabled" in slot_raw
                    and VARIANT_TODAY_HIGHLIGHTS not in slot_raw
                ):
                    variants[variant] = _normalize_variant_spec(
                        slot_raw, default_enabled=False
                    )
        elif slot_raw is not None:
            if slot in HIGHLIGHTS_SLOT_SET:
                variants[VARIANT_TODAY_HIGHLIGHTS] = _normalize_variant_spec(
                    slot_raw, default_enabled=False
                )
            else:
                variants[VARIANT_KPI_CARD] = _normalize_variant_spec(
                    slot_raw, default_enabled=True
                )
        if slot not in VISIT_REPORT_SLOTS:
            variants[VARIANT_VISIT_REPORT] = VariantSpec(enabled=False)
        if slot not in TODAY_HIGHLIGHTS_SLOT_SET:
            variants[VARIANT_TODAY_HIGHLIGHTS] = VariantSpec(enabled=False)
        if slot not in SUMMARY_MD_SLOT_SET:
            variants[VARIANT_SUMMARY_MD] = VariantSpec(enabled=False)
        slots[slot] = variants
    return ReportPushPolicy(slots=slots)


def load_report_push_policy() -> ReportPushPolicy:
    try:
        raw = SiteSetting.get_setting("report_push_policy")
    except Exception:
        return DEFAULT_REPORT_PUSH_POLICY
    return parse_report_push_policy(raw)


def skip_kpi_card_result(slot: str) -> Optional[Dict[str, Any]]:
    """kpi_card 关闭时跳过发卡；统计仍由调用方负责。"""
    if load_report_push_policy().variant_enabled(slot, VARIANT_KPI_CARD):
        return None
    return {
        "success": True,
        "skipped": True,
        "skip_reason": "kpi_card_disabled",
        "message": "kpi_card disabled",
        "recipients_count": 0,
        "success_count": 0,
    }

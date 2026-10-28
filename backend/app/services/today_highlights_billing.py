"""今日重点计费：销售对应个人日报 SKU，负责人对应团队日报 SKU。"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from app.core.config import settings
from app.services.feishu_billing_facade import (
    BillingScenario,
    check_billing_quota,
    report_billing_usage,
)
from app.services.feishu_billing_service import FEISHU_TRACE_ID_MAX_LEN
from app.utils.push_page_urls import (
    build_department_highlights_url,
    build_sales_today_highlights_url,
)

Audience = Literal["sales", "department"]

_SCENARIOS: dict[Audience, BillingScenario] = {
    "sales": BillingScenario.CRM_SALES_TODAY_HIGHLIGHTS,
    "department": BillingScenario.CRM_DEPARTMENT_TODAY_HIGHLIGHTS,
}
# 与日报 sales-daily / department-daily 错开，且带 UUID 时仍不超过飞书 trace_id 上限。
_TRACE_PREFIX: dict[Audience, str] = {
    "sales": "sales-today",
    "department": "dept-today",
}
# 超长 user_id 压成 h + sha256[:32]，与原文 user_id 分区，避免和短 id 撞键。
_COMPACT_UID_MARK = "h"
_COMPACT_UID_HEX = 32


def check_today_highlights_quota(audience: Audience) -> tuple[bool, str]:
    ok, msg, _ = check_billing_quota(_SCENARIOS[audience])
    return ok, msg


def report_today_highlights_usage(
    audience: Audience,
    *,
    report_date: date,
    user_id: str,
    review_detail: str = "",
) -> None:
    """发卡成功后上报一条。trace 按人、按日，不与日报撞单。"""
    uid = (user_id or "").strip()
    day = report_date.isoformat()
    trace_key = _usage_trace_key(audience, day, uid)
    detail = (review_detail or "").strip() or _page_url(audience, day) or settings.REVIEW_REPORT_HOST
    report_billing_usage(
        _SCENARIOS[audience],
        review_detail=detail,
        trace_key=trace_key,
        operator_user_id=uid or None,
        log_context=trace_key,
    )


def _usage_trace_key(audience: Audience, day: str, uid: str) -> str:
    prefix = _TRACE_PREFIX[audience]
    key = f"{prefix}:{day}:{uid}"
    if len(key) <= FEISHU_TRACE_ID_MAX_LEN:
        return key
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()[:_COMPACT_UID_HEX]
    compacted = f"{prefix}:{day}:{_COMPACT_UID_MARK}{digest}"
    if len(compacted) > FEISHU_TRACE_ID_MAX_LEN:
        raise ValueError(f"today-highlights usage key exceeds Feishu limit: {compacted!r}")
    return compacted


def _page_url(audience: Audience, day: str) -> str:
    if audience == "sales":
        return build_sales_today_highlights_url(day, "immediate_today")
    return build_department_highlights_url(day, "", "leader_intervene")

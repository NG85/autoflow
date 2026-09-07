"""
飞书计费统一门面：开关、trace 策略、上报与失败日志集中在一处。

开通判断顺序：
1. 租户是否使用 AI 计费包（CRM_BILLING_ENABLED）
2. 该 SKU 是否启用（读 apt_sell_billing.status：0/NULL=在用，非0=停用；缺行或读表失败视为默认开通）
3. 欠费是否放行（CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA）；查额度失败只记错误并视为充足

新增计费接入点：在 BillingScenario 增加枚举项并注册默认 ai_module_key / trace 类型。
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models.apt_sell_billing import AptSellBilling
from app.services.feishu_billing_service import (
    ACCOUNT_VISIT_PREP_GUIDE_AI_MODULE_KEY,
    SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
    SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    SIA_AI_INTERACTION_AI_MODULE_KEY,
    TEAM_WEEKLY_REPORT_AI_MODULE_KEY,
    VISIT_RECORD_AI_MODULE_KEY,
    WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY,
    feishu_billing_service,
)

logger = logging.getLogger(__name__)


class BillingScenario(str, Enum):
    """计费业务场景：与飞书 ai_module_key 及 trace 生成方式一一对应。"""

    VISIT_RECORD = "visit_record"
    SIA_CHAT = "sia_chat"
    REVIEW_SIA_CHAT = "review_sia_chat"
    CRM_SALES_PERSONAL_DAILY = "crm_sales_personal_daily"
    CRM_SALES_TEAM_DEPARTMENT_DAILY = "crm_sales_team_department_daily"
    CRM_SALES_TEAM_COMPANY_DAILY = "crm_sales_team_company_daily"
    CRM_TEAM_WEEKLY_REPORT = "crm_team_weekly_report"
    CRM_WEEKLY_FOLLOWUP_SUMMARY = "crm_weekly_followup_summary"
    ACCOUNT_VISIT_PREP_GUIDE = "account_visit_prep_guide"


_SCENARIO_MODULE_KEY: dict[BillingScenario, str] = {
    BillingScenario.VISIT_RECORD: VISIT_RECORD_AI_MODULE_KEY,
    BillingScenario.SIA_CHAT: SIA_AI_INTERACTION_AI_MODULE_KEY,
    BillingScenario.REVIEW_SIA_CHAT: SIA_AI_INTERACTION_AI_MODULE_KEY,
    BillingScenario.CRM_SALES_PERSONAL_DAILY: SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY: SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_SALES_TEAM_COMPANY_DAILY: SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_TEAM_WEEKLY_REPORT: TEAM_WEEKLY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_WEEKLY_FOLLOWUP_SUMMARY: WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY,
    BillingScenario.ACCOUNT_VISIT_PREP_GUIDE: ACCOUNT_VISIT_PREP_GUIDE_AI_MODULE_KEY,
}

# 使用随机 trace_id（每次调用一条新流水）；值为 new_trace_id 的 prefix
_RANDOM_TRACE_PREFIX: dict[BillingScenario, str] = {
    BillingScenario.SIA_CHAT: "sia-chat",
    BillingScenario.REVIEW_SIA_CHAT: "review-sia-chat",
}

FEATURE_NOT_ENABLED_MESSAGE = "该功能尚未开通，请联系管理员"
BILLING_CODE_FEATURE_NOT_ENABLED = "FEATURE_NOT_ENABLED"
BILLING_CODE_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"

_CACHE_TTL_SECONDS = 5.0
_cache_lock = threading.Lock()
_sku_enabled_cache: Optional[dict[str, bool]] = None
_cache_at: float = 0.0


def invalidate_sku_enabled_cache() -> None:
    global _sku_enabled_cache, _cache_at
    with _cache_lock:
        _sku_enabled_cache = None
        _cache_at = 0.0


def _is_unknown_column_error(exc: BaseException) -> bool:
    text = str(exc)
    return "1054" in text or "Unknown column" in text


def _sku_status_is_active(status: Any) -> bool:
    """0 或 NULL 为在用；非 0 为停用。"""
    if status is None:
        return True
    try:
        return int(status) == 0
    except (TypeError, ValueError):
        return True


def _merge_sku_status_rows(rows: list[tuple[Any, Any]]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for key, status in rows:
        key_s = str(key or "").strip()
        if not key_s:
            continue
        result[key_s] = result.get(key_s, False) or _sku_status_is_active(status)
    return result


def load_sku_enabled_map() -> dict[str, bool]:
    """
    从 apt_sell_billing 读取 ai_module_key -> 是否在用。
    缺表/读失败返回空 dict，调用方按缺行处理（默认开通）。
    status 列尚未上线时，已有 SKU 行暂按在用处理。
    """
    try:
        with Session(engine, expire_on_commit=False) as session:
            try:
                rows = session.exec(
                    select(AptSellBilling.ai_module_key, AptSellBilling.status)
                ).all()
            except Exception as col_exc:
                if not _is_unknown_column_error(col_exc):
                    raise
                logger.warning(
                    "apt_sell_billing.status is missing, treat existing SKUs as in use: %s",
                    col_exc,
                )
                session.rollback()
                rows = [
                    (key, 0)
                    for key in session.exec(select(AptSellBilling.ai_module_key)).all()
                ]
            return _merge_sku_status_rows(list(rows))
    except Exception as exc:
        logger.warning(
            "Failed to load apt_sell_billing SKU status, default all SKUs to enabled: %s",
            exc,
        )
        return {}


def get_sku_enabled_map() -> dict[str, bool]:
    global _sku_enabled_cache, _cache_at
    now = time.time()
    with _cache_lock:
        if _sku_enabled_cache is not None and (now - _cache_at) < _CACHE_TTL_SECONDS:
            return _sku_enabled_cache
    loaded = load_sku_enabled_map()
    with _cache_lock:
        _sku_enabled_cache = loaded
        _cache_at = time.time()
        return loaded


def is_sku_enabled(ai_module_key: str) -> bool:
    key = (ai_module_key or "").strip()
    if not key:
        return False
    return bool(get_sku_enabled_map().get(key, True))


def is_scenario_enabled(scenario: BillingScenario) -> bool:
    """
    计费点是否开通对应功能。

    - 租户未使用 AI 计费包（CRM_BILLING_ENABLED=False）：不拦功能。
    - 否则读 apt_sell_billing.status；缺行或读表失败视为默认开通，非 0 才关闭。
      同一 ai_module_key 的场景共用同一 SKU 状态。
    """
    if not settings.CRM_BILLING_ENABLED:
        return True
    return is_sku_enabled(module_key_for(scenario))


def module_key_for(scenario: BillingScenario) -> str:
    """额度查询 / 用量上报 / SKU 启用状态所用的 ai_module_key。"""
    return _SCENARIO_MODULE_KEY[scenario]


class BillingClientError(HTTPException):
    """HTTP 402/403，body 为结构化业务错误（code/message），不依赖 HTTP 状态码区分场景。"""

    def __init__(self, status_code: int, body: dict[str, Any]):
        self.body = body
        super().__init__(status_code=status_code, detail=body)


def register_billing_exception_handlers(app: Any) -> None:
    from fastapi.responses import JSONResponse

    @app.exception_handler(BillingClientError)
    async def _billing_client_error_handler(_request: Any, exc: BillingClientError):
        return JSONResponse(status_code=exc.status_code, content=exc.body)


def billing_client_error_body(
    code: str,
    message: str,
    *,
    feature: Optional[str] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if feature:
        body["feature"] = feature
    return body


def raise_feature_not_enabled(scenario: BillingScenario) -> None:
    raise BillingClientError(
        403,
        billing_client_error_body(
            BILLING_CODE_FEATURE_NOT_ENABLED,
            FEATURE_NOT_ENABLED_MESSAGE,
            feature=scenario.value,
        ),
    )


def raise_insufficient_quota(message: str) -> None:
    raise BillingClientError(
        402,
        billing_client_error_body(BILLING_CODE_INSUFFICIENT_FUNDS, message),
    )


def _query_quota(ai_module_key: Optional[str] = None) -> tuple[bool, str, int]:
    """
    查额度。查询失败只记错误并视为充足；额度不足仍返回 False
    （可由 CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA 放行）。
    """
    try:
        ok, msg, quota = feishu_billing_service.check_quota(ai_module_key=ai_module_key)
    except Exception as exc:
        logger.error(
            "Billing quota check failed, treat as sufficient and continue: %s",
            exc,
            exc_info=True,
        )
        return True, "quota check failed, treated as sufficient", 0
    return _pass_quota_if_allowed(ok, msg, quota)


def _pass_quota_if_allowed(ok: bool, msg: str, quota: int) -> tuple[bool, str, int]:
    """CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA 开启时，额度不足仍视为通过。"""
    if ok or not settings.CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA:
        return ok, msg, quota
    logger.warning(
        "Billing quota insufficient but CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA is enabled, continue. msg=%s quota=%s",
        msg,
        quota,
    )
    return True, msg, quota


def check_billing_quota(
    scenario: Optional[BillingScenario] = None,
    *,
    ai_module_key: Optional[str] = None,
) -> tuple[bool, str, int]:
    """
    查询租户计费额度。租户未使用 AI 计费包时不请求远端，返回 (True, 'billing disabled', 0)。

    优先使用 ``ai_module_key``；未传时由 ``scenario`` 映射到对应 SKU，
    以便远端按该功能 ``points`` 校验剩余额度是否充足。
    欠费放行（CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA）为 True 时，额度不足仍返回通过。
    查询失败只记错误并视为额度充足。
    """
    if not settings.CRM_BILLING_ENABLED:
        return True, "billing disabled", 0
    if scenario is not None and not is_scenario_enabled(scenario):
        return False, FEATURE_NOT_ENABLED_MESSAGE, 0
    module_key = ai_module_key
    if not module_key and scenario is not None:
        module_key = module_key_for(scenario)
    return _query_quota(module_key)


def check_billing_quota_for_scenarios(
    scenarios: list[BillingScenario],
) -> tuple[bool, str, int]:
    """
    按多个场景依次查额度（同一 ``ai_module_key`` 只查一次）。
    任一不足则返回失败；全部通过则返回最后一次成功结果。
    CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA 为 True 时，额度不足仍返回通过。
    查询失败只记错误并视为额度充足。
    """
    if not settings.CRM_BILLING_ENABLED:
        return True, "billing disabled", 0
    enabled = [scenario for scenario in scenarios if is_scenario_enabled(scenario)]
    if not enabled:
        return False, FEATURE_NOT_ENABLED_MESSAGE, 0

    seen_keys: set[str] = set()
    last_ok, last_msg, last_quota = True, "租户额度充足", 0
    for scenario in enabled:
        module_key = module_key_for(scenario)
        if module_key in seen_keys:
            continue
        seen_keys.add(module_key)
        last_ok, last_msg, last_quota = _query_quota(module_key)
        if not last_ok:
            return last_ok, last_msg, last_quota
    return last_ok, last_msg, last_quota


def report_billing_usage(
    scenario: BillingScenario,
    *,
    review_detail: str,
    trace_key: Optional[str] = None,
    operator_user_id: Any = None,
    log_context: Optional[str] = None,
) -> tuple[bool, int, str]:
    """
    按场景上报用量（含重试与 feishu_billing_usage_report 落库，由底层 service 完成）。

    - 随机 trace：SIA_CHAT、REVIEW_SIA_CHAT（无需 trace_key）
    - VISIT_RECORD：有 ``trace_key``（建议 ``visit-record:{record_id}``）时用确定性 trace；未传或为空时用
      ``visit-record-{uuid}`` 随机 trace（成功保存但无 record_id 时的兜底）
    - 其余场景：必须传非空 ``trace_key``（确定性 trace 会混入 ALDEBARAN_TENANT_ID，跨租户不撞单）
    - operator_user_id 为 None 时按 system 规范化（与原先 normalize 行为一致）
    """
    if not settings.CRM_BILLING_ENABLED:
        return True, 0, "billing disabled"
    if not is_scenario_enabled(scenario):
        logger.warning(
            "Skip billing report because scenario is disabled. scenario=%s",
            scenario.value,
        )
        return True, 0, "scenario disabled"

    module_key = module_key_for(scenario)
    if scenario in _RANDOM_TRACE_PREFIX:
        prefix = _RANDOM_TRACE_PREFIX[scenario]
        trace_id = feishu_billing_service.new_trace_id(prefix=prefix)
    elif scenario == BillingScenario.VISIT_RECORD:
        tk = (trace_key or "").strip()
        if tk:
            trace_id = feishu_billing_service.deterministic_trace_id(module_key, tk)
        else:
            trace_id = feishu_billing_service.new_trace_id(prefix="visit-record")
    else:
        if not trace_key or not str(trace_key).strip():
            raise ValueError(f"billing scenario {scenario.value} requires trace_key")
        trace_id = feishu_billing_service.deterministic_trace_id(module_key, str(trace_key).strip())

    operator = feishu_billing_service.normalize_operator(operator_user_id)
    ok, code, msg = feishu_billing_service.report_usage_with_retry(
        trace_id=trace_id,
        operator=operator,
        review_detail=review_detail,
        ai_module_key=module_key,
    )
    if not ok:
        suffix = f" ctx={log_context}" if log_context else ""
        logger.error(
            "Billing report failed after retries scenario=%s trace_id=%s code=%s msg=%s%s",
            scenario.value,
            trace_id,
            code,
            msg,
            suffix,
        )
    return ok, code, msg

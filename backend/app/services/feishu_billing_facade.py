"""
飞书计费统一门面：开关、trace 策略、上报与失败日志集中在一处。
新增计费接入点：在 BillingScenario 增加枚举项并在此注册 ai_module_key / trace 类型即可。
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

from app.core.config import settings
from app.services.feishu_billing_service import (
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


_SCENARIO_MODULE_KEY: dict[BillingScenario, str] = {
    BillingScenario.VISIT_RECORD: VISIT_RECORD_AI_MODULE_KEY,
    BillingScenario.SIA_CHAT: SIA_AI_INTERACTION_AI_MODULE_KEY,
    BillingScenario.REVIEW_SIA_CHAT: SIA_AI_INTERACTION_AI_MODULE_KEY,
    BillingScenario.CRM_SALES_PERSONAL_DAILY: SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY: SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_SALES_TEAM_COMPANY_DAILY: SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_TEAM_WEEKLY_REPORT: TEAM_WEEKLY_REPORT_AI_MODULE_KEY,
    BillingScenario.CRM_WEEKLY_FOLLOWUP_SUMMARY: WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY,
}

# 使用随机 trace_id（每次调用一条新流水）；值为 new_trace_id 的 prefix
_RANDOM_TRACE_PREFIX: dict[BillingScenario, str] = {
    BillingScenario.SIA_CHAT: "sia-chat",
    BillingScenario.REVIEW_SIA_CHAT: "review-sia-chat",
}


def check_billing_quota() -> tuple[bool, str, int]:
    """
    查询租户计费额度。CRM_BILLING_ENABLED 为 False 时不请求远端，返回 (True, 'billing disabled', 0)。
    """
    if not settings.CRM_BILLING_ENABLED:
        return True, "billing disabled", 0
    return feishu_billing_service.check_quota()


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
    - 其余场景：必须传非空 ``trace_key``
    - operator_user_id 为 None 时按 system 规范化（与原先 normalize 行为一致）
    """
    if not settings.CRM_BILLING_ENABLED:
        return True, 0, "billing disabled"

    module_key = _SCENARIO_MODULE_KEY[scenario]
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

"""计费 trace_id 生成：同租户幂等、跨租户隔离。"""

from app.core.config import settings
from app.services.feishu_billing_service import (
    ACCOUNT_VISIT_PREP_GUIDE_AI_MODULE_KEY,
    FEISHU_TRACE_ID_MAX_LEN,
    SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
    SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    SIA_AI_INTERACTION_AI_MODULE_KEY,
    TEAM_WEEKLY_REPORT_AI_MODULE_KEY,
    VISIT_RECORD_AI_MODULE_KEY,
    WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY,
    FeishuBillingService,
)


def test_deterministic_trace_id_stable_within_same_tenant(monkeypatch):
    monkeypatch.setattr(settings, "ALDEBARAN_TENANT_ID", "APTSELL")
    key = "department-daily:2026-08-25:华东销售部"
    prefix = "sales_team_daily_report"
    first = FeishuBillingService.deterministic_trace_id(prefix, key)
    second = FeishuBillingService.deterministic_trace_id(prefix, key)
    assert first == second
    assert first.startswith(f"{prefix}-")
    assert len(first[len(prefix) + 1 :]) == 24


def test_deterministic_trace_id_differs_across_tenants(monkeypatch):
    key = "department-daily:2026-08-25:华东销售部"
    prefix = "sales_team_daily_report"
    monkeypatch.setattr(settings, "ALDEBARAN_TENANT_ID", "CBG")
    cbg = FeishuBillingService.deterministic_trace_id(prefix, key)
    monkeypatch.setattr(settings, "ALDEBARAN_TENANT_ID", "OLM")
    olm = FeishuBillingService.deterministic_trace_id(prefix, key)
    monkeypatch.setattr(settings, "ALDEBARAN_TENANT_ID", "UPA")
    upa = FeishuBillingService.deterministic_trace_id(prefix, key)
    assert cbg != olm
    assert cbg != upa
    assert olm != upa


def test_deterministic_trace_id_fits_feishu_limit():
    key = "department-daily:2026-08-25:华东销售部"
    for prefix in (
        ACCOUNT_VISIT_PREP_GUIDE_AI_MODULE_KEY,
        SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
        SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
        SIA_AI_INTERACTION_AI_MODULE_KEY,
        TEAM_WEEKLY_REPORT_AI_MODULE_KEY,
        VISIT_RECORD_AI_MODULE_KEY,
        WEEKLY_FOLLOWUP_SUMMARY_AI_MODULE_KEY,
    ):
        trace_id = FeishuBillingService.deterministic_trace_id(prefix, key)
        assert len(trace_id) <= FEISHU_TRACE_ID_MAX_LEN, prefix
    assert len(FeishuBillingService.new_trace_id("review-sia-chat")) <= FEISHU_TRACE_ID_MAX_LEN
    assert len(FeishuBillingService.new_trace_id("visit-record")) <= FEISHU_TRACE_ID_MAX_LEN

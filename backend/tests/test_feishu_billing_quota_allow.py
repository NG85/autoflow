"""额度不足时 CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA 放行业务。"""

from app.core.config import settings
from app.services import feishu_billing_facade as facade
from app.services.feishu_billing_facade import BillingScenario


def _patch_quota(monkeypatch, *, enabled: bool, allow: bool, result: tuple[bool, str, int]):
    monkeypatch.setattr(settings, "CRM_BILLING_ENABLED", enabled)
    monkeypatch.setattr(settings, "CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA", allow)
    monkeypatch.setattr(
        facade.feishu_billing_service,
        "check_quota",
        lambda ai_module_key=None: result,
    )


def test_check_billing_quota_blocks_when_insufficient(monkeypatch):
    _patch_quota(
        monkeypatch,
        enabled=True,
        allow=False,
        result=(False, "租户额度不足，请联系管理员", 0),
    )
    ok, msg, quota = facade.check_billing_quota(BillingScenario.SIA_CHAT)
    assert ok is False
    assert quota == 0
    assert "额度不足" in msg


def test_check_billing_quota_continues_when_allow_insufficient(monkeypatch):
    _patch_quota(
        monkeypatch,
        enabled=True,
        allow=True,
        result=(False, "租户额度不足，请联系管理员", 3),
    )
    ok, msg, quota = facade.check_billing_quota(BillingScenario.VISIT_RECORD)
    assert ok is True
    assert quota == 3
    assert "额度不足" in msg


def test_check_billing_quota_for_scenarios_continues_when_allow_insufficient(monkeypatch):
    _patch_quota(
        monkeypatch,
        enabled=True,
        allow=True,
        result=(False, "租户额度不足，请联系管理员", 0),
    )
    ok, msg, _ = facade.check_billing_quota_for_scenarios(
        [BillingScenario.CRM_SALES_PERSONAL_DAILY, BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY]
    )
    assert ok is True
    assert "额度不足" in msg

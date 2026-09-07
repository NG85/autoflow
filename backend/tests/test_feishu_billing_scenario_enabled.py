"""计费点开通：租户 AI 计费包 -> apt_sell_billing.status；缺 SKU 视为默认开通。"""

import pytest

from app.core.config import settings
from app.services import feishu_billing_facade as facade
from app.services.feishu_billing_facade import (
    BILLING_CODE_FEATURE_NOT_ENABLED,
    BILLING_CODE_INSUFFICIENT_FUNDS,
    FEATURE_NOT_ENABLED_MESSAGE,
    BillingScenario,
)


def _sku_map(*enabled_keys: str, disabled: tuple[str, ...] = ()) -> dict[str, bool]:
    result = {key: True for key in enabled_keys}
    for key in disabled:
        result[key] = False
    return result


def _patch_enabled(monkeypatch, *, billing_on: bool, sku_map: dict[str, bool]):
    monkeypatch.setattr(settings, "CRM_BILLING_ENABLED", billing_on)
    monkeypatch.setattr(facade, "get_sku_enabled_map", lambda: sku_map)


def test_missing_skus_are_enabled_when_billing_on(monkeypatch):
    _patch_enabled(monkeypatch, billing_on=True, sku_map={})
    for scenario in BillingScenario:
        assert facade.is_scenario_enabled(scenario) is True


def test_load_table_failure_defaults_to_enabled(monkeypatch):
    monkeypatch.setattr(settings, "CRM_BILLING_ENABLED", True)
    facade.invalidate_sku_enabled_cache()

    class _BoomSession:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("db down")

        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(facade, "Session", _BoomSession)
    assert facade.load_sku_enabled_map() == {}
    for scenario in BillingScenario:
        assert facade.is_scenario_enabled(scenario) is True


def test_explicit_disabled_sku_blocks_when_billing_on(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map={
            "sia_ai_interaction": False,
            "behavior_data_collection_qa": False,
        },
    )
    assert facade.is_scenario_enabled(BillingScenario.SIA_CHAT) is False
    assert facade.is_scenario_enabled(BillingScenario.REVIEW_SIA_CHAT) is False
    assert facade.is_scenario_enabled(BillingScenario.VISIT_RECORD) is False
    assert facade.is_scenario_enabled(BillingScenario.ACCOUNT_VISIT_PREP_GUIDE) is True


def test_subset_disabled_by_sku(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("account_visit_prep_guide",)),
    )
    assert facade.is_scenario_enabled(BillingScenario.SIA_CHAT) is True
    assert facade.is_scenario_enabled(BillingScenario.VISIT_RECORD) is True
    assert facade.is_scenario_enabled(BillingScenario.REVIEW_SIA_CHAT) is True
    assert facade.is_scenario_enabled(BillingScenario.ACCOUNT_VISIT_PREP_GUIDE) is False


def test_shared_sku_gates_both_scenarios(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("sales_team_daily_report",)),
    )
    assert facade.is_scenario_enabled(BillingScenario.CRM_SALES_PERSONAL_DAILY) is True
    assert facade.is_scenario_enabled(BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY) is False
    assert facade.is_scenario_enabled(BillingScenario.CRM_SALES_TEAM_COMPANY_DAILY) is False

    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map("sales_team_daily_report"),
    )
    assert facade.is_scenario_enabled(BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY) is True
    assert facade.is_scenario_enabled(BillingScenario.CRM_SALES_TEAM_COMPANY_DAILY) is True


def test_billing_off_ignores_sku_flags(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=False,
        sku_map=_sku_map(disabled=("sia_ai_interaction",)),
    )
    assert facade.is_scenario_enabled(BillingScenario.SIA_CHAT) is True
    assert all(facade.is_scenario_enabled(scenario) for scenario in BillingScenario)


def test_check_quota_skips_remote_when_sku_disabled(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("sia_ai_interaction",)),
    )
    monkeypatch.setattr(settings, "CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA", False)

    def _boom(ai_module_key=None):
        raise AssertionError("check_quota should not be called for disabled SKU")

    monkeypatch.setattr(facade.feishu_billing_service, "check_quota", _boom)
    ok, msg, quota = facade.check_billing_quota(BillingScenario.SIA_CHAT)
    assert ok is False
    assert msg == FEATURE_NOT_ENABLED_MESSAGE
    assert quota == 0


def test_check_quota_for_scenarios_skips_disabled(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("sales_team_daily_report",)),
    )
    monkeypatch.setattr(
        facade.feishu_billing_service,
        "check_quota",
        lambda ai_module_key=None: (True, "租户额度充足", 10),
    )
    ok, msg, quota = facade.check_billing_quota_for_scenarios(
        [
            BillingScenario.CRM_SALES_PERSONAL_DAILY,
            BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY,
        ]
    )
    assert ok is True
    assert quota == 10


def test_check_quota_for_scenarios_all_disabled(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(
            disabled=("sales_personal_daily_report", "sales_team_daily_report"),
        ),
    )
    monkeypatch.setattr(
        facade.feishu_billing_service,
        "check_quota",
        lambda ai_module_key=None: (_ for _ in ()).throw(AssertionError("should not call")),
    )
    ok, msg, _ = facade.check_billing_quota_for_scenarios(
        [
            BillingScenario.CRM_SALES_PERSONAL_DAILY,
            BillingScenario.CRM_SALES_TEAM_COMPANY_DAILY,
        ]
    )
    assert ok is False
    assert msg == FEATURE_NOT_ENABLED_MESSAGE


def test_report_usage_skips_when_sku_disabled(monkeypatch):
    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("sia_ai_interaction",)),
    )

    def _boom(**kwargs):
        raise AssertionError("report_usage_with_retry should not be called")

    monkeypatch.setattr(
        facade.feishu_billing_service, "report_usage_with_retry", _boom
    )
    ok, code, msg = facade.report_billing_usage(
        BillingScenario.SIA_CHAT,
        review_detail="https://example.com/c/1",
    )
    assert ok is True
    assert code == 0
    assert msg == "scenario disabled"


def test_chat_sia_disabled_raises_403(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes.chat import _check_sia_quota_or_raise

    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("sia_ai_interaction",)),
    )
    with pytest.raises(HTTPException) as exc:
        _check_sia_quota_or_raise()
    assert exc.value.status_code == 403
    body = exc.value.body
    assert body["code"] == BILLING_CODE_FEATURE_NOT_ENABLED
    assert body["message"] == FEATURE_NOT_ENABLED_MESSAGE
    assert body["feature"] == "sia_chat"


def test_chat_sia_insufficient_quota_raises_402(monkeypatch):
    from fastapi import HTTPException

    from app.api.routes.chat import _check_sia_quota_or_raise

    _patch_enabled(monkeypatch, billing_on=True, sku_map={})
    monkeypatch.setattr(settings, "CRM_BILLING_ALLOW_INSUFFICIENT_QUOTA", False)
    monkeypatch.setattr(
        facade.feishu_billing_service,
        "check_quota",
        lambda ai_module_key=None: (False, "租户额度不足，请联系管理员", 0),
    )
    with pytest.raises(HTTPException) as exc:
        _check_sia_quota_or_raise()
    assert exc.value.status_code == 402
    body = exc.value.body
    assert body["code"] == BILLING_CODE_INSUFFICIENT_FUNDS
    assert "额度不足" in body["message"]
    assert "feature" not in body


def test_daily_report_types_to_run_filters_disabled(monkeypatch):
    from app.tasks.cron_jobs import _daily_report_types_to_run

    _patch_enabled(monkeypatch, billing_on=True, sku_map={})
    assert _daily_report_types_to_run(None) == ["sales", "department", "company"]
    assert _daily_report_types_to_run("department") == ["department"]
    assert _daily_report_types_to_run("sales") == ["sales"]

    _patch_enabled(
        monkeypatch,
        billing_on=True,
        sku_map=_sku_map(disabled=("sales_team_daily_report",)),
    )
    assert _daily_report_types_to_run(None) == ["sales"]
    assert _daily_report_types_to_run("department") == []


def test_merge_duplicate_sku_keys_any_in_use():
    merged = facade._merge_sku_status_rows(
        [("sia_ai_interaction", 1), ("sia_ai_interaction", 0)]
    )
    assert merged["sia_ai_interaction"] is True
    merged_null = facade._merge_sku_status_rows(
        [("sia_ai_interaction", 1), ("sia_ai_interaction", None)]
    )
    assert merged_null["sia_ai_interaction"] is True
    merged_off = facade._merge_sku_status_rows(
        [("sia_ai_interaction", 1), ("", 0)]
    )
    assert merged_off["sia_ai_interaction"] is False


def test_billing_http_body_uses_business_code_not_wrapped_in_detail():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    facade.register_billing_exception_handlers(app)

    @app.get("/feature")
    def _feature():
        facade.raise_feature_not_enabled(BillingScenario.VISIT_RECORD)

    @app.get("/funds")
    def _funds():
        facade.raise_insufficient_quota("账户欠费，服务已暂停，请充值后恢复")

    client = TestClient(app)
    feature_resp = client.get("/feature")
    assert feature_resp.status_code == 403
    assert feature_resp.json() == {
        "code": BILLING_CODE_FEATURE_NOT_ENABLED,
        "message": FEATURE_NOT_ENABLED_MESSAGE,
        "feature": "visit_record",
    }

    funds_resp = client.get("/funds")
    assert funds_resp.status_code == 402
    assert funds_resp.json() == {
        "code": BILLING_CODE_INSUFFICIENT_FUNDS,
        "message": "账户欠费，服务已暂停，请充值后恢复",
    }
    funds = facade.billing_client_error_body(
        BILLING_CODE_INSUFFICIENT_FUNDS,
        "账户欠费，服务已暂停，请充值后恢复",
    )
    assert funds == {
        "code": BILLING_CODE_INSUFFICIENT_FUNDS,
        "message": "账户欠费，服务已暂停，请充值后恢复",
    }
    feature = facade.billing_client_error_body(
        BILLING_CODE_FEATURE_NOT_ENABLED,
        FEATURE_NOT_ENABLED_MESSAGE,
        feature="visit_record",
    )
    assert feature == {
        "code": BILLING_CODE_FEATURE_NOT_ENABLED,
        "message": FEATURE_NOT_ENABLED_MESSAGE,
        "feature": "visit_record",
    }


def test_sku_status_zero_or_null_is_active():
    assert facade._sku_status_is_active(0) is True
    assert facade._sku_status_is_active(None) is True
    assert facade._sku_status_is_active(1) is False
    assert facade._sku_status_is_active(2) is False

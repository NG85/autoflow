"""今日重点计费：销售走个人日报 SKU，负责人走团队日报 SKU。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.department_today_highlights import handle_department_today_highlights
from app.services.feishu_billing_facade import BillingScenario
from app.services.report_push_policy import parse_report_push_policy
from app.services.sales_today_highlights import handle_sales_today_highlights
from app.services.feishu_billing_service import (
    FEISHU_TRACE_ID_MAX_LEN,
    SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
    SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
    FeishuBillingService,
)
from app.services.today_highlights_billing import (
    check_today_highlights_quota,
    report_today_highlights_usage,
)

DAY = date(2026, 10, 12)


def test_quota_uses_daily_report_scenarios(monkeypatch):
    seen = []

    def fake_quota(scenario):
        seen.append(scenario)
        return True, "ok", 10

    monkeypatch.setattr(
        "app.services.today_highlights_billing.check_billing_quota",
        fake_quota,
    )
    assert check_today_highlights_quota("sales") == (True, "ok")
    assert check_today_highlights_quota("department") == (True, "ok")
    assert seen == [
        BillingScenario.CRM_SALES_TODAY_HIGHLIGHTS,
        BillingScenario.CRM_DEPARTMENT_TODAY_HIGHLIGHTS,
    ]


def test_usage_trace_does_not_collide_with_daily_reports(monkeypatch):
    captured = []

    def fake_report(scenario, **kwargs):
        captured.append((scenario, kwargs))
        return True, 0, "ok"

    monkeypatch.setattr(
        "app.services.today_highlights_billing.report_billing_usage",
        fake_report,
    )
    monkeypatch.setattr(
        "app.services.today_highlights_billing.build_sales_today_highlights_url",
        lambda *args, **kwargs: "https://example.com/today",
    )
    monkeypatch.setattr(
        "app.services.today_highlights_billing.build_department_highlights_url",
        lambda *args, **kwargs: "https://example.com/dept",
    )
    report_today_highlights_usage("sales", report_date=DAY, user_id="user-1")
    report_today_highlights_usage("department", report_date=DAY, user_id="leader-1")
    sales_scenario, sales_kwargs = captured[0]
    dept_scenario, dept_kwargs = captured[1]
    assert sales_scenario is BillingScenario.CRM_SALES_TODAY_HIGHLIGHTS
    assert dept_scenario is BillingScenario.CRM_DEPARTMENT_TODAY_HIGHLIGHTS
    assert sales_kwargs["trace_key"] == "sales-today:2026-10-12:user-1"
    assert dept_kwargs["trace_key"] == "dept-today:2026-10-12:leader-1"
    assert not sales_kwargs["trace_key"].startswith("sales-daily:")
    assert not dept_kwargs["trace_key"].startswith("department-daily:")
    assert sales_kwargs["review_detail"] == "https://example.com/today"
    assert dept_kwargs["review_detail"] == "https://example.com/dept"


def test_usage_key_fits_feishu_trace_id_limit(monkeypatch):
    captured = []

    def fake_report(scenario, **kwargs):
        captured.append(kwargs)
        return True, 0, "ok"

    monkeypatch.setattr(
        "app.services.today_highlights_billing.report_billing_usage",
        fake_report,
    )
    monkeypatch.setattr(
        "app.services.today_highlights_billing.build_sales_today_highlights_url",
        lambda *args, **kwargs: "https://example.com/today",
    )
    monkeypatch.setattr(
        "app.services.today_highlights_billing.build_department_highlights_url",
        lambda *args, **kwargs: "https://example.com/dept",
    )
    user_id = str(uuid4())
    long_a = "a" * 200
    long_b = "b" * 200
    report_today_highlights_usage("sales", report_date=DAY, user_id=user_id)
    report_today_highlights_usage("department", report_date=DAY, user_id=user_id)
    report_today_highlights_usage("sales", report_date=DAY, user_id=long_a)
    report_today_highlights_usage("department", report_date=DAY, user_id=long_a)
    report_today_highlights_usage("sales", report_date=DAY, user_id=long_b)
    module_keys = (
        SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
        SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
        SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
        SALES_TEAM_DAILY_REPORT_AI_MODULE_KEY,
        SALES_PERSONAL_DAILY_REPORT_AI_MODULE_KEY,
    )
    for kwargs, module_key in zip(captured, module_keys, strict=True):
        trace_key = kwargs["trace_key"]
        trace_id = FeishuBillingService.deterministic_trace_id(module_key, trace_key)
        assert len(trace_key) <= FEISHU_TRACE_ID_MAX_LEN
        assert len(trace_id) <= FEISHU_TRACE_ID_MAX_LEN
    assert captured[0]["trace_key"] == f"sales-today:2026-10-12:{user_id}"
    assert captured[1]["trace_key"] == f"dept-today:2026-10-12:{user_id}"
    assert captured[2]["trace_key"].startswith("sales-today:2026-10-12:h")
    assert captured[3]["trace_key"].startswith("dept-today:2026-10-12:h")
    assert captured[2]["trace_key"] != captured[4]["trace_key"]
    assert long_a not in captured[2]["trace_key"]
    assert long_a not in captured[3]["trace_key"]


def test_sales_send_reports_usage(monkeypatch):
    billed = []
    user_id = str(uuid4())
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.check_today_highlights_quota",
        lambda audience: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [
            {
                "unique_id": "u1",
                "subject_type": "USER",
                "subject_id": "oauth-1",
                "audience": "sales",
                "status": "PENDING",
                "is_deleted": 0,
                "item_type": "IMMEDIATE_ACTION",
                "extract_key": "u1",
                "card_links": [{"key": "immediate_today", "title": "立即处理"}],
                "payload": {"claim": "今天打电话", "why_today": "承诺今天"},
                "report_date": DAY,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda *args, **kwargs: {"accounts": {}, "opportunities": {}},
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: SimpleNamespace(
            is_active=True,
            user_id=user_id,
            name="张三",
            department="销售",
            oauth_user=SimpleNamespace(open_id="ou_1", provider="feishu"),
        ),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.deliver_highlight_card",
        lambda *args, **kwargs: {"success": True},
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.report_today_highlights_usage",
        lambda audience, **kwargs: billed.append((audience, kwargs)),
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert billed == [
        ("sales", {"report_date": DAY, "user_id": user_id}),
    ]


def test_sales_quota_blocks_before_send(monkeypatch):
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.check_today_highlights_quota",
        lambda audience: (False, "该功能尚未开通，请联系管理员"),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: sent.__setitem__("load", True) or [],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.deliver_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=DAY)
    assert result["skip_reason"] == "billing_blocked"
    assert sent["count"] == 0
    assert "load" not in sent


def test_sales_failed_send_does_not_bill(monkeypatch):
    billed = []
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"sales_daily": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.check_today_highlights_quota",
        lambda audience: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_sales_extract_rows",
        lambda *args, **kwargs: [
            {
                "unique_id": "u1",
                "subject_type": "USER",
                "subject_id": "oauth-1",
                "audience": "sales",
                "status": "PENDING",
                "is_deleted": 0,
                "item_type": "IMMEDIATE_ACTION",
                "extract_key": "u1",
                "card_links": [{"key": "immediate_today", "title": "立即处理"}],
                "payload": {"claim": "今天打电话", "why_today": "承诺今天"},
                "report_date": DAY,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.load_display_names",
        lambda *args, **kwargs: {"accounts": {}, "opportunities": {}},
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: SimpleNamespace(
            is_active=True,
            user_id=str(uuid4()),
            name="张三",
            department="销售",
            oauth_user=SimpleNamespace(open_id="ou_1", provider="feishu"),
        ),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.deliver_highlight_card",
        lambda *args, **kwargs: {"success": False, "message": "send failed"},
    )
    monkeypatch.setattr(
        "app.services.sales_today_highlights.report_today_highlights_usage",
        lambda *args, **kwargs: billed.append(1),
    )
    result = handle_sales_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 0
    assert billed == []


def test_department_send_reports_usage(monkeypatch):
    billed = []
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"department_highlights": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.check_today_highlights_quota",
        lambda audience: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.services.oauth_service.oauth_client.get_departments_with_leaders",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_insight_reports",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: [
            {
                "unique_id": "u1",
                "subject_type": "USER",
                "subject_id": "oauth-leader",
                "audience": "leader",
                "status": "PENDING",
                "is_deleted": 0,
                "item_type": "TODAY_INTERVENTION",
                "extract_key": "u1",
                "card_links": [{"key": "leader_intervene", "title": "今日需介入"}],
                "payload": {"claim": "拍板", "related_user_id": "oauth-zhang"},
                "report_date": DAY,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_display_names",
        lambda *args, **kwargs: {
            "accounts": {},
            "opportunities": {},
            "people": {"oauth-zhang": "张三"},
        },
    )
    monkeypatch.setattr(
        "app.repositories.user_profile.user_profile_repo.get_by_oauth_user_id",
        lambda session, oauth_id: SimpleNamespace(
            is_active=True,
            user_id="leader-1",
            oauth_user_id=oauth_id,
            name="王经理",
            department="华北销售",
            oauth_user=SimpleNamespace(open_id="ou_leader", provider="feishu"),
        ),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.filter_opted_out_recipients",
        lambda session, recipients, **kwargs: list(recipients),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_leader_visit_watch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: {"success": True},
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.report_today_highlights_usage",
        lambda audience, **kwargs: billed.append((audience, kwargs)),
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["success_count"] == 1
    assert billed == [
        ("department", {"report_date": DAY, "user_id": "leader-1"}),
    ]


def test_department_quota_blocks_before_send(monkeypatch):
    sent = {"count": 0}
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_report_push_policy",
        lambda: parse_report_push_policy({"department_highlights": {"today_highlights": True}}),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.check_today_highlights_quota",
        lambda audience: (False, "额度不足"),
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.load_department_extract_rows",
        lambda *args, **kwargs: sent.__setitem__("load", True) or [],
    )
    monkeypatch.setattr(
        "app.services.department_today_highlights.deliver_department_highlight_card",
        lambda *args, **kwargs: sent.__setitem__("count", sent["count"] + 1) or {"success": True},
    )
    result = handle_department_today_highlights(MagicMock(), report_date=DAY)
    assert result["skip_reason"] == "billing_blocked"
    assert sent["count"] == 0
    assert "load" not in sent

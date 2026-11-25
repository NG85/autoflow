"""日报计费只在消息真正发出后上报。kpi_card 关闭时 success 仍为 True。"""

from datetime import date
from unittest.mock import MagicMock

from app.services.crm_statistics_service import CRMStatisticsService
from app.services.feishu_billing_facade import BillingScenario

TARGET = date(2026, 11, 23)
SKIPPED = {
    "success": True,
    "skipped": True,
    "skip_reason": "kpi_card_disabled",
    "message": "kpi_card disabled",
    "recipients_count": 0,
    "success_count": 0,
}
DELIVERED = {
    "success": True,
    "message": "sent",
    "recipients_count": 1,
    "success_count": 1,
    "operator_user_id": "op-1",
}


def test_daily_push_delivered_requires_a_real_send():
    assert not CRMStatisticsService._daily_push_delivered(SKIPPED)
    assert not CRMStatisticsService._daily_push_delivered(
        {"success": True, "success_count": 0}
    )
    assert not CRMStatisticsService._daily_push_delivered(
        {"success": False, "message": "failed", "success_count": 0}
    )
    assert CRMStatisticsService._daily_push_delivered(DELIVERED)


def _capture_billing(monkeypatch):
    calls = []

    def _report(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "app.services.crm_statistics_service.report_billing_usage",
        _report,
    )
    return calls


def test_sales_daily_skipped_push_does_not_bill(monkeypatch):
    calls = _capture_billing(monkeypatch)
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.send_sales_daily_report_notification",
        lambda **kwargs: SKIPPED,
    )
    CRMStatisticsService()._send_sales_daily_report_notifications(
        MagicMock(),
        [
            {
                "recorder_id": "sales-1",
                "recorder": "张三",
                "department_name": "销售一部",
                "report_date": TARGET,
                "visit_detail_page": "https://example/sales",
            }
        ],
    )
    assert calls == []


def test_sales_daily_bills_after_card_is_delivered(monkeypatch):
    calls = _capture_billing(monkeypatch)
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.send_sales_daily_report_notification",
        lambda **kwargs: DELIVERED,
    )
    CRMStatisticsService()._send_sales_daily_report_notifications(
        MagicMock(),
        [
            {
                "recorder_id": "sales-1",
                "recorder": "张三",
                "department_name": "销售一部",
                "report_date": TARGET,
                "visit_detail_page": "https://example/sales",
            }
        ],
    )
    assert len(calls) == 1
    assert calls[0][0][0] == BillingScenario.CRM_SALES_PERSONAL_DAILY
    assert calls[0][1]["trace_key"] == "sales-daily:2026-11-23:sales-1"


def _department_report():
    return {
        "department_name": "销售一部",
        "visit_detail_page": "https://example/dept",
        "has_summary_record": True,
        "statistics": [
            {
                "end_customer_total_follow_up": 2,
                "partner_total_follow_up": 0,
                "lead_total_follow_up": 0,
            }
        ],
    }


def _patch_department_sources(monkeypatch, send_result):
    monkeypatch.setattr(
        "app.services.crm_statistics_service.oauth_client.get_departments_with_leaders",
        lambda: {"销售一部": [{"userId": "mgr-1", "name": "李四"}]},
    )
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.get_department_names_with_review_group",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        CRMStatisticsService,
        "aggregate_department_reports",
        lambda self, **kwargs: [_department_report()],
    )
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.send_department_daily_report_notification",
        lambda **kwargs: send_result,
    )


def test_department_daily_skipped_push_does_not_bill(monkeypatch):
    calls = _capture_billing(monkeypatch)
    _patch_department_sources(monkeypatch, SKIPPED)
    CRMStatisticsService()._generate_and_send_department_daily_reports(MagicMock(), TARGET)
    assert calls == []


def test_department_daily_bills_when_follow_up_card_is_delivered(monkeypatch):
    calls = _capture_billing(monkeypatch)
    _patch_department_sources(monkeypatch, DELIVERED)
    CRMStatisticsService()._generate_and_send_department_daily_reports(MagicMock(), TARGET)
    assert len(calls) == 1
    assert calls[0][0][0] == BillingScenario.CRM_SALES_TEAM_DEPARTMENT_DAILY
    assert calls[0][1]["trace_key"] == "department-daily:2026-11-23:销售一部"
    assert calls[0][1]["operator_user_id"] == "mgr-1"


def test_department_daily_empty_text_still_does_not_bill(monkeypatch):
    calls = _capture_billing(monkeypatch)
    report = _department_report()
    report["statistics"] = [
        {
            "end_customer_total_follow_up": 0,
            "partner_total_follow_up": 0,
            "lead_total_follow_up": 0,
        }
    ]
    monkeypatch.setattr(
        "app.services.crm_statistics_service.oauth_client.get_departments_with_leaders",
        lambda: {"销售一部": [{"userId": "mgr-1"}]},
    )
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.get_department_names_with_review_group",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        CRMStatisticsService,
        "aggregate_department_reports",
        lambda self, **kwargs: [report],
    )
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.send_department_daily_report_notification",
        lambda **kwargs: DELIVERED,
    )
    CRMStatisticsService()._generate_and_send_department_daily_reports(MagicMock(), TARGET)
    assert calls == []


def _patch_company(monkeypatch, report, send_result):
    monkeypatch.setattr(
        CRMStatisticsService,
        "aggregate_company_report",
        lambda self, session, target_date: report,
    )
    monkeypatch.setattr(
        "app.services.crm_statistics_service.platform_notification_service.send_company_daily_report_notification",
        lambda **kwargs: send_result,
    )


def test_company_daily_skipped_push_does_not_bill(monkeypatch):
    calls = _capture_billing(monkeypatch)
    _patch_company(
        monkeypatch,
        {
            "visit_detail_page": "https://example/company",
            "has_summary_record": True,
            "statistics": [{"end_customer_total_follow_up": 4}],
        },
        SKIPPED,
    )
    CRMStatisticsService()._generate_and_send_company_daily_report(MagicMock(), TARGET)
    assert calls == []


def test_company_daily_bills_when_follow_up_card_is_delivered(monkeypatch):
    calls = _capture_billing(monkeypatch)
    _patch_company(
        monkeypatch,
        {
            "visit_detail_page": "https://example/company",
            "has_summary_record": True,
            "statistics": [{"end_customer_total_follow_up": 4}],
        },
        DELIVERED,
    )
    CRMStatisticsService()._generate_and_send_company_daily_report(MagicMock(), TARGET)
    assert len(calls) == 1
    assert calls[0][0][0] == BillingScenario.CRM_SALES_TEAM_COMPANY_DAILY
    assert calls[0][1]["trace_key"] == "company-daily:2026-11-23"
    assert calls[0][1]["operator_user_id"] == "op-1"


def test_company_daily_empty_text_still_does_not_bill(monkeypatch):
    calls = _capture_billing(monkeypatch)
    _patch_company(
        monkeypatch,
        {
            "visit_detail_page": "https://example/company",
            "has_summary_record": True,
            "statistics": [
                {
                    "end_customer_total_follow_up": 0,
                    "partner_total_follow_up": 0,
                    "lead_total_follow_up": 0,
                }
            ],
        },
        DELIVERED,
    )
    CRMStatisticsService()._generate_and_send_company_daily_report(MagicMock(), TARGET)
    assert calls == []

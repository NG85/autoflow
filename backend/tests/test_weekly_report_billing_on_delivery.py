"""周报计费只在卡片真正发出且本周已生成时上报。"""

from app.services.crm_statistics_service import CRMStatisticsService

SKIPPED = {
    "success": True,
    "skipped": True,
    "skip_reason": "kpi_card_disabled",
    "message": "kpi_card disabled",
    "recipients_count": 0,
    "success_count": 0,
}
CARD_SENT = {
    "success": True,
    "message": "sent",
    "recipients_count": 1,
    "success_count": 1,
}
TEXT_SENT = {
    "success": True,
    "message": "text sent",
    "recipients_count": 1,
    "success_count": 1,
}
HAS_DATA = {"has_data": True, "department_name": "销售一部"}
NO_DATA = {"has_data": False, "department_name": "销售一部"}


def test_weekly_does_not_bill_when_kpi_card_is_skipped():
    assert not CRMStatisticsService.weekly_report_should_bill(SKIPPED, HAS_DATA)


def test_weekly_bills_when_generated_card_is_delivered():
    assert CRMStatisticsService.weekly_report_should_bill(CARD_SENT, HAS_DATA)


def test_weekly_does_not_bill_ungenerated_text():
    assert not CRMStatisticsService.weekly_report_should_bill(TEXT_SENT, NO_DATA)


def test_weekly_does_not_bill_when_send_fails():
    failed = {"success": False, "message": "failed", "success_count": 0}
    assert not CRMStatisticsService.weekly_report_should_bill(failed, HAS_DATA)

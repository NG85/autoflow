"""部门/公司日报「是否有跟进」判定单测。"""

from app.services.crm_statistics_service import CRMStatisticsService


def test_daily_report_has_follow_up_true_when_any_bucket_positive():
    assert CRMStatisticsService.daily_report_has_follow_up(
        {"statistics": [{"end_customer_total_follow_up": 1}]}
    )
    assert CRMStatisticsService.daily_report_has_follow_up(
        {"statistics": [{"partner_total_follow_up": 2}]}
    )
    assert CRMStatisticsService.daily_report_has_follow_up(
        {"statistics": [{"lead_total_follow_up": 3}]}
    )


def test_daily_report_has_follow_up_false_when_empty_or_zero():
    assert not CRMStatisticsService.daily_report_has_follow_up(None)
    assert not CRMStatisticsService.daily_report_has_follow_up({})
    assert not CRMStatisticsService.daily_report_has_follow_up({"statistics": []})
    assert not CRMStatisticsService.daily_report_has_follow_up(
        {
            "statistics": [
                {
                    "end_customer_total_follow_up": 0,
                    "partner_total_follow_up": 0,
                    "lead_total_follow_up": 0,
                }
            ]
        }
    )

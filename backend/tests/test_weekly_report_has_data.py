"""周报计费以 Aldebaran metadata.has_data 为准。"""

from app.services.crm_statistics_service import CRMStatisticsService


def test_weekly_report_has_data_reads_metadata_flag():
    assert CRMStatisticsService.weekly_report_has_data(
        {"metadata": {"has_data": True}, "performance_overview": {}}
    )
    assert not CRMStatisticsService.weekly_report_has_data(
        {"metadata": {"has_data": False}, "performance_overview": {}}
    )


def test_weekly_report_has_data_true_even_when_kpis_are_zero():
    assert CRMStatisticsService.weekly_report_has_data(
        {
            "metadata": {"has_data": True},
            "performance_overview": {
                "weekly_closed_deals": {
                    "summary": {
                        "new_closed_amount": 0,
                        "customers_count": 0,
                        "opportunities_count": 0,
                    },
                    "deals_formatted": [],
                }
            },
        }
    )


def test_weekly_report_has_data_false_when_missing_or_empty():
    assert not CRMStatisticsService.weekly_report_has_data(None)
    assert not CRMStatisticsService.weekly_report_has_data({})
    assert not CRMStatisticsService.weekly_report_has_data({"metadata": {}})
    assert not CRMStatisticsService.weekly_report_has_data({"has_data": False})


def test_weekly_report_has_data_reads_rebuilt_top_level_flag():
    assert CRMStatisticsService.weekly_report_has_data({"has_data": True})
    assert not CRMStatisticsService.weekly_report_has_data({"has_data": False})
    assert CRMStatisticsService.weekly_report_has_data({"has_data": "true"})
    assert not CRMStatisticsService.weekly_report_has_data({"has_data": "false"})

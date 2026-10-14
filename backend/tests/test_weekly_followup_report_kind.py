"""crm_weekly_followup_summary.report_kind 区分周跟进总结与周拜访报告。"""

from datetime import date

from app.models.crm_weekly_followup_summary import (
    CRMWeeklyFollowupSummary,
    REPORT_KIND_FOLLOWUP,
    REPORT_KIND_VISIT_REPORT,
)


def test_report_kind_defaults_to_followup():
    row = CRMWeeklyFollowupSummary(
        week_start=date(2026, 9, 13),
        week_end=date(2026, 9, 19),
        summary_type="company",
    )
    assert row.report_kind == REPORT_KIND_FOLLOWUP


def test_visit_report_kind_can_be_set():
    row = CRMWeeklyFollowupSummary(
        week_start=date(2026, 9, 13),
        week_end=date(2026, 9, 19),
        summary_type="company",
        report_kind=REPORT_KIND_VISIT_REPORT,
        summary_content="## 周拜访报告",
    )
    assert row.report_kind == REPORT_KIND_VISIT_REPORT


def test_unique_constraint_includes_report_kind():
    table = CRMWeeklyFollowupSummary.__table__
    ux = next(c for c in table.constraints if c.name == "ux_crm_weekly_followup_summary")
    assert [col.name for col in ux.columns] == [
        "week_start",
        "week_end",
        "summary_type",
        "department_name",
        "report_kind",
    ]

"""department_group_chats.notification_type：拜访 / 日报分流匹配。"""

from app.services.platform_notification_service import (
    _department_group_entry_matches_notification_type,
)


def _entry(notification_type: str) -> dict:
    return {"notification_type": notification_type}


def test_department_review_visits_matches_visit_only():
    entry = _entry("department_review_visits")
    assert _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="visit"
    )
    assert not _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="report"
    )


def test_department_review_reports_matches_report_only():
    entry = _entry("department_review_reports")
    assert not _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="visit"
    )
    assert _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="report"
    )


def test_department_review_matches_both_visit_and_report():
    entry = _entry("department_review")
    assert _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="visit"
    )
    assert _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="report"
    )


def test_all_matches_both_visit_and_report():
    entry = _entry("all")
    assert _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="visit"
    )
    assert _department_group_entry_matches_notification_type(
        entry, "department_review", review_scope="report"
    )

"""推送页面链接构建工具测试。"""

from app.utils import push_page_urls as urls


def test_build_visit_list_page_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    monkeypatch.setattr(urls.settings, "VISIT_DETAIL_PAGE_URL", "/v2/behavior")
    result = urls.build_visit_list_page_url(
        start_date="2026-06-26",
        end_date="2026-06-26",
        department_name="华东销售部",
    )
    assert result == (
        "https://example.com/v2/behavior"
        "?visit_communication_date_start=2026-06-26"
        "&visit_communication_date_end=2026-06-26"
        "&department_name=%E5%8D%8E%E4%B8%9C%E9%94%80%E5%94%AE%E9%83%A8"
    )


def test_visit_list_base_supports_legacy_full_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    monkeypatch.setattr(
        urls.settings,
        "VISIT_DETAIL_PAGE_URL",
        "https://legacy.example.com/v2/behavior",
    )
    assert urls._visit_list_base() == "https://legacy.example.com/v2/behavior"


def test_build_visit_record_page_and_billing_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    monkeypatch.setattr(urls.settings, "VISIT_DETAIL_PAGE_URL", "/v2/behavior")

    assert urls.build_visit_record_page_url("form_abc") == "https://example.com/v2/behavior/form_abc"
    assert urls.build_visit_record_billing_page_url("form_abc") == "https://example.com/v2/behavior?form_abc"


def test_build_task_list_page_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    monkeypatch.setattr(urls.settings, "CRM_SALES_TASK_PAGE_URL", "/v2/task")
    result = urls.build_task_list_page_url(
        owner_name="张三",
        due_date__lte="2026-06-22",
        is_overdue=True,
        ai_status__in=["PENDING", "IN_PROGRESS"],
    )
    assert "owner_name=" in result
    assert "is_overdue=true" in result
    assert "ai_status__in=PENDING" in result
    assert "ai_status__in=IN_PROGRESS" in result
    assert result.startswith("https://example.com/v2/task?")


def test_task_list_base_supports_legacy_full_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    monkeypatch.setattr(
        urls.settings,
        "CRM_SALES_TASK_PAGE_URL",
        "https://legacy.example.com/v2/task",
    )
    assert urls._task_list_base() == "https://legacy.example.com/v2/task"


def test_build_weekly_urls(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert (
        urls.build_weekly_review_1_page_url("exec_1")
        == "https://example.com/v2/business/weekly-review/exec_1"
    )
    assert (
        urls.build_weekly_review_5_page_url("exec_5")
        == "https://example.com/v2/business/behavior-analysis/exec_5"
    )
    assert urls.build_weekly_followup_summary_page_url(
        week_start="2026-06-16",
        week_end="2026-06-22",
    ) == (
        "https://example.com/v2/business/followup-summary/detail"
        "?week_start=2026-06-16&week_end=2026-06-22"
    )

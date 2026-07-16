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


def test_build_visit_list_page_url_with_recorder(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    monkeypatch.setattr(urls.settings, "VISIT_DETAIL_PAGE_URL", "/v2/behavior")
    result = urls.build_visit_list_page_url(
        start_date="2026-06-26",
        end_date="2026-06-26",
        recorder="张三",
    )
    assert result == (
        "https://example.com/v2/behavior"
        "?visit_communication_date_start=2026-06-26"
        "&visit_communication_date_end=2026-06-26"
        "&recorder=%E5%BC%A0%E4%B8%89"
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
    assert (
        urls.build_visit_record_add_comment_page_url("form_20260618_080952_666_1f16aed1")
        == "https://example.com/v2/behavior/form_20260618_080952_666_1f16aed1/add-comment"
    )
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


def test_build_weekly_review_1_page_url(monkeypatch):
    """部门/公司周报计费与卡片 weekly_review_1_page 共用此链接。"""
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert (
        urls.build_weekly_review_1_page_url("exec_1")
        == "https://example.com/v2/business/weekly-review/exec_1"
    )
    assert (
        urls.build_weekly_review_1_page_url("exec/with space")
        == "https://example.com/v2/business/weekly-review/exec%2Fwith%20space"
    )
    assert urls.build_weekly_review_1_page_url("") == "https://example.com"
    assert urls.build_weekly_review_1_page_url("   ") == "https://example.com"


def test_build_weekly_review_1_page_url_without_host(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "")
    assert urls.build_weekly_review_1_page_url("exec_1") == ""


def test_build_weekly_review_5_page_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert (
        urls.build_weekly_review_5_page_url("exec_5")
        == "https://example.com/v2/business/behavior-analysis/exec_5"
    )
    assert urls.build_weekly_review_5_page_url("") == "https://example.com"


def test_build_weekly_followup_summary_page_url_company(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert urls.build_weekly_followup_summary_page_url(
        week_start="2026-06-16",
        week_end="2026-06-22",
    ) == (
        "https://example.com/v2/business/followup-summary/detail"
        "?week_start=2026-06-16&week_end=2026-06-22"
    )


def test_build_weekly_followup_summary_page_url_department(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert urls.build_weekly_followup_summary_page_url(
        week_start="2026-06-16",
        week_end="2026-06-22",
        department_name="华东销售部",
    ) == (
        "https://example.com/v2/business/followup-summary/detail"
        "?week_start=2026-06-16&week_end=2026-06-22"
        "&department_name=%E5%8D%8E%E4%B8%9C%E9%94%80%E5%94%AE%E9%83%A8"
    )


def test_build_weekly_followup_summary_page_url_without_host(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "")
    assert (
        urls.build_weekly_followup_summary_page_url(
            week_start="2026-06-16",
            week_end="2026-06-22",
            department_name="华东销售部",
        )
        == ""
    )


def test_build_visit_guide_page_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert (
        urls.build_visit_guide_page_url("019e8715-9fd8-7d5d-9b96-5f499dacfb1e")
        == "https://example.com/v2/business/visit-guide/019e8715-9fd8-7d5d-9b96-5f499dacfb1e"
    )
    assert (
        urls.build_visit_guide_page_url("id/with space")
        == "https://example.com/v2/business/visit-guide/id%2Fwith%20space"
    )
    assert (
        urls.build_visit_guide_page_url(None)
        == "https://example.com/v2/business/visit-guide/history"
    )
    assert (
        urls.build_visit_guide_page_url("")
        == "https://example.com/v2/business/visit-guide/history"
    )
    assert (
        urls.build_visit_guide_page_url("   ")
        == "https://example.com/v2/business/visit-guide/history"
    )


def test_build_visit_guide_page_url_without_host(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "")
    assert urls.build_visit_guide_page_url("019e8715-9fd8-7d5d-9b96-5f499dacfb1e") == ""
    assert urls.build_visit_guide_page_url(None) == ""


def test_build_sia_chat_page_url(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    assert (
        urls.build_sia_chat_page_url("019e8715-9fd8-7d5d-9b96-5f499dacfb1e")
        == "https://example.com/v2/c/019e8715-9fd8-7d5d-9b96-5f499dacfb1e"
    )
    assert (
        urls.build_sia_chat_page_url("id/with space")
        == "https://example.com/v2/c/id%2Fwith%20space"
    )
    assert urls.build_sia_chat_page_url(None) == "https://example.com/v2/c"
    assert urls.build_sia_chat_page_url("") == "https://example.com/v2/c"
    assert urls.build_sia_chat_page_url("   ") == "https://example.com/v2/c"


def test_build_sia_chat_page_url_without_host(monkeypatch):
    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "")
    assert urls.build_sia_chat_page_url("019e8715-9fd8-7d5d-9b96-5f499dacfb1e") == ""
    assert urls.build_sia_chat_page_url(None) == ""


def test_chat_review_detail_helpers_use_v2_paths(monkeypatch):
    """chat.py 计费入口应走 /v2/c 与 /v2/business/visit-guide。"""
    from app.api.routes import chat as chat_routes

    monkeypatch.setattr(urls.settings, "REVIEW_REPORT_HOST", "https://example.com")
    chat_id = "019e8715-9fd8-7d5d-9b96-5f499dacfb1e"

    assert (
        chat_routes._build_chat_review_detail(chat_id)
        == f"https://example.com/v2/c/{chat_id}"
    )
    assert chat_routes._build_chat_review_detail(None) == "https://example.com/v2/c"

    assert (
        chat_routes._build_visit_prep_review_detail(chat_id)
        == f"https://example.com/v2/business/visit-guide/{chat_id}"
    )
    assert (
        chat_routes._build_visit_prep_review_detail(None)
        == "https://example.com/v2/business/visit-guide/history"
    )


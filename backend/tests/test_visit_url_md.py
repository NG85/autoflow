"""visit_url_md：推送卡片 Markdown 超链接字段。"""

from unittest.mock import patch

from app.services.platform_notification_service import PlatformNotificationService


def test_build_visit_url_md_http_https():
    url = "https://mi5p6bgsnf8.feishu.cn/minutes/obcn123"
    assert PlatformNotificationService._build_visit_url_md(url) == f"[{url}]({url})"
    assert (
        PlatformNotificationService._build_visit_url_md("http://example.com/a")
        == "[http://example.com/a](http://example.com/a)"
    )


def test_build_visit_url_md_local_upload_links_to_detail_page():
    path = "aptsell/data/customer-uploads/1774236579732-u86f7_附件4 接入飞书SSO登录和机器人.docx"
    with patch(
        "app.utils.push_page_urls.build_visit_record_page_url",
        return_value="https://app.example/v2/behavior/rec-1",
    ) as build_url:
        assert (
            PlatformNotificationService._build_visit_url_md(path, record_id="rec-1")
            == f"[{path}](https://app.example/v2/behavior/rec-1)"
        )
        build_url.assert_called_once_with("rec-1")


def test_build_visit_url_md_data_customer_uploads_without_tenant():
    """真实卡片样例：无 tenant 前缀的 data/customer-uploads/ 路径。"""
    path = (
        "data/customer-uploads/1769417965628-8cbll_1769137760057-wtsy2_"
        "1765515782712-w9del_1761099776108-39182.docx"
    )
    with patch(
        "app.utils.push_page_urls.build_visit_record_page_url",
        return_value="https://app.example/v2/behavior/rec-meeting",
    ) as build_url:
        assert (
            PlatformNotificationService._build_visit_url_md(path, record_id="rec-meeting")
            == f"[{path}](https://app.example/v2/behavior/rec-meeting)"
        )
        build_url.assert_called_once_with("rec-meeting")


def test_build_visit_url_md_storage_path_prefix_links_to_detail_page():
    path = "aptsell/data/customer-uploads/report.docx"
    with patch(
        "app.services.platform_notification_service.settings.STORAGE_PATH_PREFIX",
        "aptsell/data/customer-uploads/",
    ), patch(
        "app.utils.push_page_urls.build_visit_record_page_url",
        return_value="https://app.example/v2/behavior/rec-2",
    ):
        assert (
            PlatformNotificationService._build_visit_url_md(path, record_id="rec-2")
            == f"[{path}](https://app.example/v2/behavior/rec-2)"
        )


def test_build_visit_url_md_local_upload_without_detail_url():
    with patch(
        "app.utils.push_page_urls.build_visit_record_page_url",
        return_value="",
    ):
        assert (
            PlatformNotificationService._build_visit_url_md(
                "aptsell/data/customer-uploads/a.docx",
                record_id="rec-x",
            )
            == "--"
        )


def test_build_visit_url_md_dingtalk_room_code_passthrough():
    assert PlatformNotificationService._build_visit_url_md("8921437924") == "8921437924"


def test_build_visit_url_md_empty():
    assert PlatformNotificationService._build_visit_url_md(None) == "--"
    assert PlatformNotificationService._build_visit_url_md("  ") == "--"


def test_prepare_visit_record_template_vars_sets_visit_url_md_keeps_visit_url():
    svc = PlatformNotificationService()
    visit_url = "https://example.feishu.cn/docx/abc"
    visit_record = {
        "visit_url": visit_url,
        "last_modified_time": "2026-08-10 10:00:00",
        "department": "销售部",
        "collaborative_participants": None,
    }
    with patch(
        "app.crm.save_engine.generate_dynamic_fields_for_visit_record",
        return_value=[],
    ), patch(
        "app.utils.push_page_urls.build_visit_record_add_comment_page_url",
        return_value="https://app/comment",
    ):
        vars_ = svc._prepare_visit_record_template_vars(
            record_id="rec-1",
            recorder_name="张三",
            visit_record=visit_record,
            meeting_notes="notes",
            risk_info=None,
        )

    expected_md = f"[{visit_url}]({visit_url})"
    assert visit_record["visit_url"] == visit_url
    assert visit_record["visit_url_md"] == expected_md
    assert vars_["sales_visit_records"][0]["visit_url"] == visit_url
    assert vars_["sales_visit_records"][0]["visit_url_md"] == expected_md


def test_prepare_visit_record_template_vars_local_path_uses_detail_url():
    svc = PlatformNotificationService()
    path = "aptsell/data/customer-uploads/report.docx"
    visit_record = {
        "visit_url": path,
        "last_modified_time": "2026-08-10 10:00:00",
        "department": "销售部",
        "collaborative_participants": None,
    }
    with patch(
        "app.crm.save_engine.generate_dynamic_fields_for_visit_record",
        return_value=[],
    ), patch(
        "app.utils.push_page_urls.build_visit_record_add_comment_page_url",
        return_value="https://app/comment",
    ), patch(
        "app.utils.push_page_urls.build_visit_record_page_url",
        return_value="https://app.example/v2/behavior/rec-9",
    ):
        svc._prepare_visit_record_template_vars(
            record_id="rec-9",
            recorder_name="张三",
            visit_record=visit_record,
            meeting_notes="notes",
            risk_info=None,
        )

    assert visit_record["visit_url"] == path
    assert visit_record["visit_url_md"] == f"[{path}](https://app.example/v2/behavior/rec-9)"

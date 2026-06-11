"""DocumentProcessingService routing: 听记分支不影响原有类型。"""

from unittest.mock import patch

from app.platforms.utils.url_parser import (
    parse_bitable_url,
    parse_dingtalk_notable_url,
    parse_dingtalk_transcribe_url,
    parse_platform_document_url,
)
from app.services.document_processing_service import DocumentProcessingService


def _service() -> DocumentProcessingService:
    return DocumentProcessingService()


def test_parse_dingtalk_transcribe_url_only_matches_shanji():
    assert (
        parse_dingtalk_transcribe_url(
            "https://shanji.dingtalk.com/app/transcribes/abc123"
        )
        == "abc123"
    )
    assert parse_dingtalk_transcribe_url("https://feishu.cn/docx/abc123") is None
    assert parse_dingtalk_transcribe_url("https://alidocs.dingtalk.com/i/nodes/x") is None
    assert parse_dingtalk_transcribe_url("123456789") is None


def test_parse_dingtalk_notable_url_uses_sheet_id_param():
    base_id, sheet_id = parse_dingtalk_notable_url(
        "https://alidocs.dingtalk.com/i/nodes/qnYxxx?sheetId=sheet1"
    )
    assert base_id == "qnYxxx"
    assert sheet_id == "sheet1"
    assert parse_dingtalk_notable_url(
        "https://alidocs.dingtalk.com/i/nodes/qnYxxx?sheet=legacy"
    ) == ("qnYxxx", None)


def test_parse_bitable_url_unchanged():
    url_type, token, table_id, view_id = parse_bitable_url(
        "https://example.feishu.cn/base/appToken?table=tblXXX&view=vewYYY"
    )
    assert url_type == "base"
    assert token == "appToken"
    assert table_id == "tblXXX"
    assert view_id == "vewYYY"


def test_room_code_routes_to_conference_handler():
    svc = _service()
    with patch.object(svc, "_handle_dingtalk_conference", return_value={"ok": "conf"}) as conf, patch.object(
        svc, "_handle_dingtalk_transcribe"
    ) as transcribe, patch.object(svc, "_handle_platform_document") as platform, patch.object(
        svc, "_handle_local_document"
    ) as local:
        result = svc.process_document_url("123456789", user_id="u1")

    assert result == {"ok": "conf"}
    conf.assert_called_once_with("123456789")
    transcribe.assert_not_called()
    platform.assert_not_called()
    local.assert_not_called()


def test_transcribe_url_routes_to_transcribe_handler():
    svc = _service()
    url = "https://shanji.dingtalk.com/app/transcribes/task001"
    with patch.object(svc, "_handle_dingtalk_conference") as conf, patch.object(
        svc, "_handle_dingtalk_transcribe", return_value={"ok": "transcribe"}
    ) as transcribe, patch.object(svc, "_handle_platform_document") as platform, patch.object(
        svc, "_handle_local_document"
    ) as local:
        result = svc.process_document_url(url, user_id="u1")

    assert result == {"ok": "transcribe"}
    conf.assert_not_called()
    transcribe.assert_called_once_with(url)
    platform.assert_not_called()
    local.assert_not_called()


def test_feishu_url_routes_to_platform_handler():
    svc = _service()
    url = "https://example.feishu.cn/docx/abc123"
    with patch.object(svc, "_handle_dingtalk_conference") as conf, patch.object(
        svc, "_handle_dingtalk_transcribe"
    ) as transcribe, patch.object(
        svc, "_handle_platform_document", return_value={"ok": "feishu"}
    ) as platform, patch.object(svc, "_handle_local_document") as local:
        result = svc.process_document_url(url, user_id="u1", auth_code="code")

    assert result == {"ok": "feishu"}
    conf.assert_not_called()
    transcribe.assert_not_called()
    platform.assert_called_once_with(url, "u1", "code", "feishu")
    local.assert_not_called()


def test_lark_url_routes_to_platform_handler():
    svc = _service()
    url = "https://example.larksuite.com/docx/abc123"
    with patch.object(svc, "_handle_dingtalk_conference") as conf, patch.object(
        svc, "_handle_dingtalk_transcribe"
    ) as transcribe, patch.object(
        svc, "_handle_platform_document", return_value={"ok": "lark"}
    ) as platform, patch.object(svc, "_handle_local_document") as local:
        result = svc.process_document_url(url, user_id="u1")

    assert result == {"ok": "lark"}
    conf.assert_not_called()
    transcribe.assert_not_called()
    platform.assert_called_once_with(url, "u1", None, "lark")
    local.assert_not_called()


def test_other_http_url_routes_to_local_handler():
    svc = _service()
    url = "https://alidocs.dingtalk.com/i/nodes/abc123"
    with patch.object(svc, "_handle_dingtalk_conference") as conf, patch.object(
        svc, "_handle_dingtalk_transcribe"
    ) as transcribe, patch.object(svc, "_handle_platform_document") as platform, patch.object(
        svc, "_handle_local_document", return_value={"ok": "local"}
    ) as local:
        result = svc.process_document_url(url, user_id="u1")

    assert result == {"ok": "local"}
    conf.assert_not_called()
    transcribe.assert_not_called()
    platform.assert_not_called()
    local.assert_called_once_with(url)


def test_local_storage_path_routes_to_local_handler():
    svc = _service()
    url = "pingcap/data/customer-uploads/report.docx"
    with patch.object(svc, "_handle_dingtalk_conference") as conf, patch.object(
        svc, "_handle_dingtalk_transcribe"
    ) as transcribe, patch.object(svc, "_handle_platform_document") as platform, patch.object(
        svc, "_handle_local_document", return_value={"ok": "local"}
    ) as local:
        result = svc.process_document_url(url, user_id="u1")

    assert result == {"ok": "local"}
    conf.assert_not_called()
    transcribe.assert_not_called()
    platform.assert_not_called()
    local.assert_called_once_with(url)


def test_feishu_minutes_url_still_parsed_as_minutes():
    url_type, token = parse_platform_document_url(
        "https://example.feishu.cn/minutes/min123"
    )
    assert url_type == "minutes"
    assert token == "min123"


def test_is_dingtalk_room_code_boundary():
    svc = _service()
    assert svc._is_dingtalk_room_code("123456") is True
    assert svc._is_dingtalk_room_code("123456789012") is True
    assert svc._is_dingtalk_room_code("12345") is False
    assert svc._is_dingtalk_room_code("1234567890123") is False
    assert svc._is_dingtalk_room_code("123456a") is False
    assert svc._is_dingtalk_room_code("https://shanji.dingtalk.com/app/transcribes/x") is False

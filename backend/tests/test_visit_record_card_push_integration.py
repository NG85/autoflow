"""拜访卡片推送：token 重试与 partial_pushed 集成行为。"""

from unittest.mock import patch

import pytest
import requests

from app.services.platform_notification_service import PlatformNotificationService
from app.services.visit_record_card_push_status import VisitRecordCardPushStatus


def _token_error():
    import json
    from unittest.mock import MagicMock

    response = MagicMock()
    response.text = json.dumps(
        {"code": 99991663, "msg": "Invalid access token for authorization."}
    )
    response.status_code = 400
    response.json.return_value = json.loads(response.text)
    exc = requests.HTTPError(response=response)
    exc.response = response
    return exc


def test_visit_record_individual_send_reuses_refreshed_token_for_next_recipient():
    """第一个接收人触发刷新后，同批后续接收人直接复用新 token，不再失败重试。"""
    svc = PlatformNotificationService()
    recipients = {
        "feishu": [
            {
                "open_id": "ou_recorder",
                "name": "销售",
                "type": "recorder",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_leader",
                "name": "上级",
                "type": "leader",
                "receive_id_type": "open_id",
            },
        ]
    }
    token_error = _token_error()
    platform_tokens = {"feishu": "stale_token"}

    def dispatch(open_id, token, content, platform, receive_id_type, **kwargs):
        if token == "stale_token":
            raise token_error
        return {"code": 0}

    with patch.object(svc, "_validate_platform_support", return_value=True), patch.object(
        svc, "_get_platform_tokens", return_value=platform_tokens
    ), patch.object(svc, "_get_visit_record_template_id", return_value="tpl"), patch.object(
        svc, "_dispatch_platform_message", side_effect=dispatch
    ) as mock_dispatch, patch.object(
        svc, "_invalidate_tenant_access_token"
    ), patch.object(
        svc, "_get_tenant_access_token", return_value="fresh_token"
    ):
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {}
        )

    assert count == 2
    assert failed == []
    assert platform_tokens["feishu"] == "fresh_token"
    assert mock_dispatch.call_count == 3
    assert mock_dispatch.call_args_list[0].args[1] == "stale_token"
    assert mock_dispatch.call_args_list[1].args[1] == "fresh_token"
    assert mock_dispatch.call_args_list[2].args[1] == "fresh_token"


def test_visit_record_individual_send_recovers_from_stale_token():
    """记录人 token 失效后重试成功，且全部接收人成功时为 pushed。"""
    svc = PlatformNotificationService()
    recipients = {
        "feishu": [
            {
                "open_id": "ou_recorder",
                "name": "销售",
                "type": "recorder",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_leader",
                "name": "上级",
                "type": "leader",
                "receive_id_type": "open_id",
            },
        ]
    }
    token_error = _token_error()

    with patch.object(svc, "_validate_platform_support", return_value=True), patch.object(
        svc, "_get_platform_tokens", return_value={"feishu": "stale_token"}
    ), patch.object(svc, "_get_visit_record_template_id", return_value="tpl"), patch.object(
        svc,
        "_dispatch_platform_message",
        side_effect=[token_error, {"code": 0}, {"code": 0}],
    ) as mock_dispatch, patch.object(
        svc, "_invalidate_tenant_access_token"
    ) as mock_invalidate, patch.object(
        svc, "_get_tenant_access_token", return_value="fresh_token"
    ):
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {}
        )

    assert count == 2
    assert failed == []
    mock_invalidate.assert_called_once_with("feishu")
    assert mock_dispatch.call_count == 3
    assert mock_dispatch.call_args_list[1].args[1] == "fresh_token"


def test_send_visit_record_notification_partial_pushed_when_one_fails():
    svc = PlatformNotificationService()
    recipients = {
        "feishu": [
            {
                "open_id": "ou_recorder",
                "name": "销售",
                "type": "recorder",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_leader",
                "name": "上级",
                "type": "leader",
                "receive_id_type": "open_id",
            },
        ]
    }

    with patch.object(
        svc,
        "_collect_visit_record_recipients_and_groups",
        return_value=(recipients, [], []),
    ), patch.object(
        svc,
        "_send_visit_record_to_individual_recipients",
        return_value=(1, [{"name": "销售", "platform": "feishu", "error": "boom"}]),
    ), patch.object(svc, "_send_visit_record_to_review_groups"), patch.object(
        svc, "_send_visit_record_to_brief_groups"
    ):
        result = svc.send_visit_record_notification(
            db_session=None,
            record_id="rec_1",
            recorder_name="销售",
            recorder_id="uid_1",
            visit_record={"recorder": "销售"},
        )

    assert result["success_count"] == 1
    assert result["card_push_status"] == VisitRecordCardPushStatus.PARTIAL_PUSHED


def test_send_message_does_not_retry_more_than_once():
    svc = PlatformNotificationService()
    token_error = _token_error()

    with patch.object(
        svc, "_dispatch_platform_message", side_effect=[token_error, token_error]
    ), patch.object(svc, "_invalidate_tenant_access_token"), patch.object(
        svc, "_get_tenant_access_token", return_value="fresh_token"
    ):
        with pytest.raises(requests.HTTPError):
            svc._send_message("ou_1", "stale", {"text": "hi"}, "feishu")


def test_send_visit_record_notification_retry_only_failed_recipients():
    svc = PlatformNotificationService()
    failed_snapshot = [
        {
            "open_id": "ou_recorder",
            "name": "销售",
            "type": "recorder",
            "platform": "feishu",
            "receive_id_type": "open_id",
            "error": "token",
        }
    ]
    override = {
        "feishu": [
            {
                "open_id": "ou_recorder",
                "name": "销售",
                "type": "recorder",
                "receive_id_type": "open_id",
                "platform": "feishu",
            }
        ]
    }

    with patch.object(
        svc, "_prepare_visit_record_template_vars", return_value={}
    ), patch.object(
        svc,
        "_send_visit_record_to_individual_recipients",
        return_value=(1, []),
    ) as mock_send, patch.object(
        svc, "_send_visit_record_to_review_groups"
    ) as mock_review, patch.object(
        svc, "_send_visit_record_to_brief_groups"
    ) as mock_brief:
        result = svc.send_visit_record_notification(
            db_session=None,
            record_id="rec_1",
            recorder_name="销售",
            visit_record={"recorder": "销售"},
            recipients_by_platform_override=override,
            skip_group_notifications=True,
            total_recipients_count_override=2,
            previously_failed_count=1,
        )

    assert result["is_retry"] is True
    assert result["card_push_status"] == VisitRecordCardPushStatus.PUSHED
    assert result["success_count"] == 2
    mock_send.assert_called_once()
    mock_review.assert_not_called()
    mock_brief.assert_not_called()

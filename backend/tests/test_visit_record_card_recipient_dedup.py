"""拜访记录卡片推送：同一人只推一张，保留优先级最高的角色。"""

from unittest.mock import patch

from app.services.platform_notification_service import PlatformNotificationService


def _patch_send_deps(svc: PlatformNotificationService):
    return (
        patch.object(svc, "_validate_platform_support", return_value=True),
        patch.object(svc, "_get_platform_tokens", return_value={"feishu": "token"}),
    )


def test_visit_record_dedupes_same_template_same_person():
    svc = PlatformNotificationService()
    recipients = {
        "feishu": [
            {
                "open_id": "ou_123",
                "name": "李华",
                "type": "configured_cc",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_123",
                "name": "李华",
                "type": "collaborative_participant",
                "receive_id_type": "open_id",
            },
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", return_value="tpl_link"
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "link", {}
        )

    assert count == 1
    assert failed == []
    assert mock_send.call_count == 1


def test_visit_record_dedupes_leader_template_over_collaborator():
    svc = PlatformNotificationService()

    def template_for_role(recipient_type, platform, visit_type, form_type):
        if recipient_type in ("recorder", "collaborative_participant"):
            return "tpl_recorder"
        return "tpl_leader"

    recipients = {
        "feishu": [
            {
                "open_id": "ou_456",
                "name": "王芳",
                "type": "configured_cc",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_456",
                "name": "王芳",
                "type": "collaborative_participant",
                "receive_id_type": "open_id",
            },
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", side_effect=template_for_role
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {"form_type": "complete"}
        )

    assert count == 1
    assert failed == []
    assert mock_send.call_count == 1
    card_content = mock_send.call_args[0][2]
    assert card_content["data"]["template_id"] == "tpl_leader"


def test_visit_record_dedupes_leader_over_configured_cc():
    svc = PlatformNotificationService()

    def template_for_role(recipient_type, platform, visit_type, form_type):
        if recipient_type in ("recorder", "collaborative_participant"):
            return "tpl_recorder"
        return "tpl_leader"

    recipients = {
        "feishu": [
            {
                "open_id": "ou_456",
                "name": "王芳",
                "type": "configured_cc",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_456",
                "name": "王芳",
                "type": "leader",
                "receive_id_type": "open_id",
            },
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", side_effect=template_for_role
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {"form_type": "complete"}
        )

    assert count == 1
    assert failed == []
    assert mock_send.call_count == 1
    card_content = mock_send.call_args[0][2]
    assert card_content["data"]["template_id"] == "tpl_leader"


def test_visit_record_keeps_highest_priority_role():
    svc = PlatformNotificationService()
    recipients = {
        "feishu": [
            {
                "open_id": "ou_789",
                "name": "陈明",
                "type": "collaborative_participant",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_789",
                "name": "陈明",
                "type": "recorder",
                "receive_id_type": "open_id",
            },
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", return_value="tpl_link"
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "link", {}
        )

    assert count == 1
    assert failed == []
    assert mock_send.call_count == 1

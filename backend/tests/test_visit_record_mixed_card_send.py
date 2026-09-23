"""拜访推送：同一回调里按角色混发 recap_lite 与 legacy。"""

from unittest.mock import patch

from app.services.platform_notification_service import PlatformNotificationService
from app.services.visit_record_push_policy import parse_visit_record_push_policy
from app.services.visit_record_recap_card import RecapLiteCard


def _patch_send_deps(svc: PlatformNotificationService, platforms=None):
    tokens = {p: "token" for p in (platforms or ["feishu"])}
    return (
        patch.object(svc, "_validate_platform_support", return_value=True),
        patch.object(svc, "_get_platform_tokens", return_value=tokens),
    )


def _recap_card() -> RecapLiteCard:
    return RecapLiteCard(
        title="正常 · 9月20日 · 星辰科技",
        header_template="green",
        feishu_body="飞书复盘正文",
        dingtalk_text="### 正常 · 9月20日 · 星辰科技\n钉钉复盘正文",
    )


def test_mixed_policy_sends_lite_to_recorder_and_legacy_to_leader():
    svc = PlatformNotificationService()
    policy = parse_visit_record_push_policy(
        {
            "recipients": {
                "recorder": "recap_lite",
                "leader": "legacy",
            }
        }
    )
    recipients = {
        "feishu": [
            {"open_id": "ou_leader", "name": "上级", "type": "leader", "receive_id_type": "open_id"},
            {"open_id": "ou_recorder", "name": "销售", "type": "recorder", "receive_id_type": "open_id"},
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", return_value="tpl_leader"
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients,
            {"recorder": "销售"},
            "form",
            {"form_type": "complete"},
            policy=policy,
            recap_card=_recap_card(),
        )

    assert count == 2
    assert failed == []
    assert mock_send.call_count == 2
    by_open_id = {call.args[0]: call for call in mock_send.call_args_list}
    recorder_payload = by_open_id["ou_recorder"].args[2]
    leader_payload = by_open_id["ou_leader"].args[2]
    assert recorder_payload["schema"] == "2.0"
    assert recorder_payload["header"]["template"] == "green"
    assert recorder_payload["header"]["title"]["content"] == "正常 · 9月20日 · 星辰科技"
    assert leader_payload["type"] == "template"
    assert leader_payload["data"]["template_id"] == "tpl_leader"
    assert by_open_id["ou_recorder"].kwargs["msg_type"] == "interactive"
    assert by_open_id["ou_leader"].kwargs["msg_type"] == "interactive"


def test_same_person_keeps_recorder_lite_not_cc_legacy():
    svc = PlatformNotificationService()
    policy = parse_visit_record_push_policy(
        {
            "recipients": {
                "recorder": "recap_lite",
                "configured_cc": "legacy",
            }
        }
    )
    recipients = {
        "feishu": [
            {
                "open_id": "ou_same",
                "name": "李华",
                "type": "configured_cc",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_same",
                "name": "李华",
                "type": "recorder",
                "receive_id_type": "open_id",
            },
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", return_value="tpl_legacy"
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {}, policy=policy, recap_card=_recap_card()
        )

    assert count == 1
    assert failed == []
    payload = mock_send.call_args.args[2]
    assert payload["schema"] == "2.0"
    assert payload["header"]["template"] == "green"


def test_dingtalk_recap_lite_uses_text_channel():
    svc = PlatformNotificationService()
    policy = parse_visit_record_push_policy({"recipients": {"recorder": "recap_lite"}})
    recipients = {
        "dingtalk": [
            {"open_id": "ding_1", "name": "销售", "type": "recorder", "receive_id_type": "open_id"}
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc, platforms=["dingtalk"])
    with validate_patch, tokens_patch, patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {}, policy=policy, recap_card=_recap_card()
        )

    assert count == 1
    assert failed == []
    assert mock_send.call_args.args[2] == _recap_card().dingtalk_text
    assert mock_send.call_args.kwargs["msg_type"] == "text"


def test_all_legacy_still_uses_templates_without_recap_payload():
    svc = PlatformNotificationService()
    recipients = {
        "feishu": [
            {"open_id": "ou_recorder", "name": "销售", "type": "recorder", "receive_id_type": "open_id"}
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(
        svc, "_get_visit_record_template_id", return_value="tpl_recorder"
    ), patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients, {}, "form", {}
        )

    assert count == 1
    assert failed == []
    assert mock_send.call_args.args[2]["data"]["template_id"] == "tpl_recorder"


def test_sales_and_leader_recap_lite_use_different_view_cards():
    svc = PlatformNotificationService()
    policy = parse_visit_record_push_policy({"strategy": "recap_lite"})
    sales_card = RecapLiteCard(
        title="正常 · 9月20日 · 星辰科技",
        header_template="green",
        feishu_body="销售视角摘要",
        dingtalk_text="### 正常 · 9月20日 · 星辰科技\n销售视角摘要",
    )
    leader_card = RecapLiteCard(
        title="需关注 · 9月20日 · 星辰科技",
        header_template="orange",
        feishu_body="上级视角摘要",
        dingtalk_text="### 需关注 · 9月20日 · 星辰科技\n上级视角摘要",
    )
    recipients = {
        "feishu": [
            {"open_id": "ou_leader", "name": "上级", "type": "leader", "receive_id_type": "open_id"},
            {"open_id": "ou_recorder", "name": "销售", "type": "recorder", "receive_id_type": "open_id"},
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients,
            {},
            "form",
            {},
            policy=policy,
            recap_cards_by_view={"sales": sales_card, "leader": leader_card},
        )

    assert count == 2
    assert failed == []
    by_open_id = {call.args[0]: call for call in mock_send.call_args_list}
    assert by_open_id["ou_recorder"].args[2]["header"]["title"]["content"] == "正常 · 9月20日 · 星辰科技"
    assert by_open_id["ou_recorder"].args[2]["body"]["elements"][0]["content"] == "销售视角摘要"
    assert by_open_id["ou_leader"].args[2]["header"]["title"]["content"] == "需关注 · 9月20日 · 星辰科技"
    assert by_open_id["ou_leader"].args[2]["body"]["elements"][0]["content"] == "上级视角摘要"


def test_collaborator_who_is_also_leader_gets_leader_recap_card():
    svc = PlatformNotificationService()
    policy = parse_visit_record_push_policy({"strategy": "recap_lite"})
    sales_card = RecapLiteCard(
        title="正常 · 9月20日 · 星辰科技",
        header_template="green",
        feishu_body="销售视角摘要",
        dingtalk_text="### 正常 · 9月20日 · 星辰科技\n销售视角摘要",
    )
    leader_card = RecapLiteCard(
        title="需关注 · 9月20日 · 星辰科技",
        header_template="orange",
        feishu_body="上级视角摘要",
        dingtalk_text="### 需关注 · 9月20日 · 星辰科技\n上级视角摘要",
    )
    recipients = {
        "feishu": [
            {
                "open_id": "ou_same",
                "name": "王芳",
                "type": "collaborative_participant",
                "receive_id_type": "open_id",
            },
            {
                "open_id": "ou_same",
                "name": "王芳",
                "type": "leader",
                "receive_id_type": "open_id",
            },
        ]
    }
    validate_patch, tokens_patch = _patch_send_deps(svc)
    with validate_patch, tokens_patch, patch.object(svc, "_send_message") as mock_send:
        count, failed = svc._send_visit_record_to_individual_recipients(
            recipients,
            {},
            "form",
            {},
            policy=policy,
            recap_cards_by_view={"sales": sales_card, "leader": leader_card},
        )

    assert count == 1
    assert failed == []
    payload = mock_send.call_args.args[2]
    assert payload["header"]["title"]["content"] == "需关注 · 9月20日 · 星辰科技"
    assert payload["body"]["elements"][0]["content"] == "上级视角摘要"

"""拜访记录卡片推送：configured_cc 与 department_review 过滤逻辑。"""

from unittest.mock import MagicMock, patch

from app.services.platform_notification_service import PlatformNotificationService


def test_visit_record_priority_leader_over_configured_cc():
    svc = PlatformNotificationService()
    assert (
        svc._VISIT_RECORD_RECIPIENT_TYPE_PRIORITY["leader"]
        < svc._VISIT_RECORD_RECIPIENT_TYPE_PRIORITY["configured_cc"]
    )


@patch("app.services.platform_notification_service.resolve_visit_record_cc_recipients")
@patch.object(PlatformNotificationService, "get_recipients_for_recorder")
@patch.object(PlatformNotificationService, "_get_collaborative_participants_recipients", return_value={})
@patch.object(PlatformNotificationService, "_get_group_chats_by_department")
def test_collect_visit_record_removes_global_cc_when_department_review_group_exists(
    mock_get_groups,
    _mock_collab,
    mock_get_recorder,
    mock_resolve_cc,
):
    svc = PlatformNotificationService()
    mock_get_recorder.return_value = {
        "feishu": [
            {"open_id": "ou_leader", "type": "leader", "name": "上级"},
            {"open_id": "ou_recorder", "type": "recorder", "name": "销售"},
        ]
    }
    mock_resolve_cc.return_value = {
        "feishu": [
            {
                "open_id": "ou_cc_global",
                "type": "configured_cc",
                "cc_scope": "global",
                "name": "全局抄送人",
            },
        ]
    }
    mock_get_groups.side_effect = [
        [{"platform": "feishu", "chat_id": "oc_review"}],
        [],
    ]

    recipients, review_groups, brief_groups = svc._collect_visit_record_recipients_and_groups(
        MagicMock(),
        recorder_name="销售",
        recorder_id="recorder-1",
        visit_record={"recorder_department_name": "华东"},
    )

    feishu_types = {item["type"] for item in recipients["feishu"]}
    assert "leader" not in feishu_types
    assert "configured_cc" not in feishu_types
    assert "recorder" in feishu_types
    assert review_groups
    assert brief_groups == []


@patch("app.services.platform_notification_service.resolve_visit_record_cc_recipients")
@patch.object(PlatformNotificationService, "get_recipients_for_recorder")
@patch.object(PlatformNotificationService, "_get_collaborative_participants_recipients", return_value={})
@patch.object(PlatformNotificationService, "_get_group_chats_by_department")
def test_collect_visit_record_keeps_user_scoped_cc_when_department_review_group_exists(
    mock_get_groups,
    _mock_collab,
    mock_get_recorder,
    mock_resolve_cc,
):
    svc = PlatformNotificationService()
    mock_get_recorder.return_value = {
        "feishu": [
            {"open_id": "ou_leader", "type": "leader", "name": "上级"},
            {"open_id": "ou_recorder", "type": "recorder", "name": "销售"},
        ]
    }
    mock_resolve_cc.return_value = {
        "feishu": [
            {
                "open_id": "ou_cc_user",
                "type": "configured_cc",
                "cc_scope": "user",
                "name": "个人抄送人",
            },
            {
                "open_id": "ou_cc_global",
                "type": "configured_cc",
                "cc_scope": "global",
                "name": "全局抄送人",
            },
        ]
    }
    mock_get_groups.side_effect = [
        [{"platform": "feishu", "chat_id": "oc_review"}],
        [],
    ]

    recipients, _, _ = svc._collect_visit_record_recipients_and_groups(
        MagicMock(),
        recorder_name="销售",
        recorder_id="recorder-1",
        visit_record={"recorder_department_name": "华东"},
    )

    feishu_recipients = recipients["feishu"]
    assert len(feishu_recipients) == 2
    cc_recipients = [r for r in feishu_recipients if r["type"] == "configured_cc"]
    assert len(cc_recipients) == 1
    assert cc_recipients[0]["open_id"] == "ou_cc_user"
    assert cc_recipients[0]["cc_scope"] == "user"

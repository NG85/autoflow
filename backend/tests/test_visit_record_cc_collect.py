"""拜访记录卡片推送：configured_cc 与 department_review 过滤逻辑。"""

from unittest.mock import MagicMock, patch

from app.services.platform_notification_service import PlatformNotificationService
from app.services.visit_record_push_policy import (
    DEFAULT_VISIT_RECORD_PUSH_POLICY,
    parse_visit_record_push_policy,
)


def test_visit_record_priority_leader_over_configured_cc():
    svc = PlatformNotificationService()
    assert (
        svc._VISIT_RECORD_RECIPIENT_TYPE_PRIORITY["leader"]
        < svc._VISIT_RECORD_RECIPIENT_TYPE_PRIORITY["configured_cc"]
    )


@patch("app.services.platform_notification_service.resolve_visit_record_cc_recipients", return_value={})
@patch.object(PlatformNotificationService, "get_recipients_for_recorder")
@patch.object(PlatformNotificationService, "_get_collaborative_participants_recipients")
@patch.object(PlatformNotificationService, "_get_group_chats_by_department", return_value=[])
def test_collect_skips_collaborator_already_assigned_leader(
    _mock_groups,
    mock_collab,
    mock_get_recorder,
    _mock_resolve_cc,
):
    svc = PlatformNotificationService()
    mock_get_recorder.return_value = {
        "feishu": [
            {"open_id": "ou_leader", "type": "leader", "name": "上级"},
            {"open_id": "ou_recorder", "type": "recorder", "name": "销售"},
        ]
    }
    mock_collab.return_value = {
        "feishu": [
            {"open_id": "ou_leader", "type": "collaborative_participant", "name": "上级"},
            {"open_id": "ou_peer", "type": "collaborative_participant", "name": "同事"},
        ]
    }

    recipients, _, _ = svc._collect_visit_record_recipients_and_groups(
        MagicMock(),
        recorder_name="销售",
        recorder_id="recorder-1",
        visit_record={},
    )

    feishu = recipients["feishu"]
    by_open_id = {item["open_id"]: item["type"] for item in feishu}
    assert by_open_id["ou_leader"] == "leader"
    assert by_open_id["ou_peer"] == "collaborative_participant"
    assert len(feishu) == 3


@patch("app.services.platform_notification_service.resolve_visit_record_cc_recipients", return_value={})
@patch.object(PlatformNotificationService, "get_recipients_for_recorder")
@patch.object(PlatformNotificationService, "_get_collaborative_participants_recipients")
@patch.object(PlatformNotificationService, "_get_group_chats_by_department")
def test_leader_who_is_collaborator_is_not_kept_as_sales_when_review_group_exists(
    mock_groups,
    mock_collab,
    mock_get_recorder,
    _mock_resolve_cc,
):
    svc = PlatformNotificationService()
    mock_get_recorder.return_value = {
        "feishu": [
            {"open_id": "ou_leader", "type": "leader", "name": "上级"},
            {"open_id": "ou_recorder", "type": "recorder", "name": "销售"},
        ]
    }
    mock_collab.return_value = {
        "feishu": [
            {"open_id": "ou_leader", "type": "collaborative_participant", "name": "上级"},
            {"open_id": "ou_peer", "type": "collaborative_participant", "name": "同事"},
        ]
    }
    mock_groups.side_effect = [
        [{"platform": "feishu", "chat_id": "oc_review"}],
        [],
    ]

    recipients, _, _ = svc._collect_visit_record_recipients_and_groups(
        MagicMock(),
        recorder_name="销售",
        recorder_id="recorder-1",
        visit_record={},
        policy=DEFAULT_VISIT_RECORD_PUSH_POLICY,
    )

    by_open_id = {item["open_id"]: item["type"] for item in recipients["feishu"]}
    assert "ou_leader" not in by_open_id
    assert by_open_id["ou_recorder"] == "recorder"
    assert by_open_id["ou_peer"] == "collaborative_participant"


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
        policy=DEFAULT_VISIT_RECORD_PUSH_POLICY,
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
        policy=DEFAULT_VISIT_RECORD_PUSH_POLICY,
    )

    feishu_recipients = recipients["feishu"]
    assert len(feishu_recipients) == 2
    cc_recipients = [r for r in feishu_recipients if r["type"] == "configured_cc"]
    assert len(cc_recipients) == 1
    assert cc_recipients[0]["open_id"] == "ou_cc_user"
    assert cc_recipients[0]["cc_scope"] == "user"


@patch("app.services.platform_notification_service.resolve_visit_record_cc_recipients")
@patch.object(PlatformNotificationService, "get_recipients_for_recorder")
@patch.object(PlatformNotificationService, "_get_collaborative_participants_recipients", return_value={})
@patch.object(PlatformNotificationService, "_get_group_chats_by_department")
def test_collect_visit_record_removes_department_scoped_cc_when_department_review_group_exists(
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
                "open_id": "ou_cc_dept",
                "type": "configured_cc",
                "cc_scope": "department",
                "name": "部门抄送人",
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
        visit_record={
            "recorder_department_id": "dept-east",
            "recorder_department_name": "华东",
        },
        policy=DEFAULT_VISIT_RECORD_PUSH_POLICY,
    )

    mock_resolve_cc.assert_called_once()
    assert mock_resolve_cc.call_args.kwargs["recorder_department_id"] == "dept-east"
    cc_recipients = [r for r in recipients["feishu"] if r["type"] == "configured_cc"]
    assert len(cc_recipients) == 1
    assert cc_recipients[0]["open_id"] == "ou_cc_user"
    assert cc_recipients[0]["cc_scope"] == "user"


def _review_group_recipients(policy):
    svc = PlatformNotificationService()
    with patch.object(
        svc,
        "get_recipients_for_recorder",
        return_value={
            "feishu": [
                {"open_id": "ou_leader", "type": "leader", "name": "上级"},
                {"open_id": "ou_recorder", "type": "recorder", "name": "销售"},
            ]
        },
    ), patch.object(
        svc, "_get_collaborative_participants_recipients", return_value={}
    ), patch.object(
        svc,
        "_get_group_chats_by_department",
        side_effect=[[{"platform": "feishu", "chat_id": "oc_review"}], []],
    ), patch(
        "app.services.platform_notification_service.resolve_visit_record_cc_recipients",
        return_value={
            "feishu": [
                {
                    "open_id": "ou_cc_global",
                    "type": "configured_cc",
                    "cc_scope": "global",
                    "name": "全局抄送人",
                }
            ]
        },
    ):
        recipients, review_groups, _brief = svc._collect_visit_record_recipients_and_groups(
            MagicMock(),
            recorder_name="销售",
            recorder_id="recorder-1",
            visit_record={"recorder_department_name": "华东"},
            policy=policy,
        )
    return {item["type"] for item in recipients["feishu"]}, review_groups


def test_empty_replaces_keeps_leader_and_global_cc_beside_the_review_group():
    types, review_groups = _review_group_recipients(
        parse_visit_record_push_policy(
            {"groups": {"review": {"card": "legacy", "replaces": []}}}
        )
    )
    assert review_groups
    assert types == {"leader", "recorder", "configured_cc"}


def test_replacing_only_the_leader_keeps_global_cc():
    types, _review_groups = _review_group_recipients(
        parse_visit_record_push_policy(
            {"groups": {"review": {"replaces": ["leader"]}}}
        )
    )
    assert "leader" not in types
    assert "configured_cc" in types
    assert "recorder" in types


def test_disabled_review_group_does_not_remove_the_leader():
    types, review_groups = _review_group_recipients(
        parse_visit_record_push_policy({"groups": {"review": False}})
    )
    assert review_groups
    assert "leader" in types
    assert "configured_cc" in types

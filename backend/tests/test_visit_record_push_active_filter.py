"""拜访卡片推送：仅向 is_active 的 user profile 推送。"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.services.platform_notification_service import PlatformNotificationService


def test_filter_recipients_by_active_profiles_keeps_only_active():
    svc = PlatformNotificationService()
    db_session = MagicMock()
    recipients = {
        "feishu": [
            {
                "open_id": "ou_active",
                "name": "在职",
                "type": "leader",
            },
            {
                "open_id": "ou_inactive",
                "name": "停用",
                "type": "leader",
            },
        ]
    }

    with patch(
        "app.services.platform_notification_service.user_profile_repo.get_active_open_ids",
        return_value={"ou_active"},
    ):
        filtered = svc._filter_recipients_by_active_profiles(db_session, recipients)

    assert filtered == {
        "feishu": [
            {
                "open_id": "ou_active",
                "name": "在职",
                "type": "leader",
            }
        ]
    }


def test_get_recipients_for_recorder_skips_inactive_leaders():
    svc = PlatformNotificationService()
    db_session = MagicMock()
    recorder_profile = MagicMock()
    recorder_profile.name = "销售"
    recorder_profile.user_id = "uid-1"
    recorder_profile.department = "华东"
    recorder_profile.oauth_users = [
        MagicMock(provider="feishu", open_id="ou_recorder", user_id="uid-1"),
    ]

    with patch.object(
        svc, "_resolve_recorder_profile", return_value=recorder_profile
    ), patch.object(
        svc,
        "_get_reporting_chain_leaders",
        return_value=[
            {
                "platform": "feishu",
                "open_id": "ou_active_leader",
                "name": "在职上级",
                "department": "华东",
            },
            {
                "platform": "feishu",
                "open_id": "ou_inactive_leader",
                "name": "停用上级",
                "department": "华东",
            },
        ],
    ), patch(
        "app.services.platform_notification_service.user_profile_repo.get_active_open_ids",
        return_value={"ou_active_leader"},
    ):
        recipients = svc.get_recipients_for_recorder(
            db_session,
            recorder_name="销售",
            recorder_id="recorder-1",
            recorder_profile=recorder_profile,
        )

    assert [item["open_id"] for item in recipients["feishu"]] == [
        "ou_recorder",
        "ou_active_leader",
    ]


def test_get_recipients_for_recorder_picks_newer_provider():
    """记录人同时绑定飞书与钉钉时，只推 update_time 较新的平台。"""
    svc = PlatformNotificationService()
    db_session = MagicMock()
    recorder_profile = MagicMock()
    recorder_profile.name = "销售"
    recorder_profile.user_id = "uid-1"
    recorder_profile.department = "华东"
    recorder_profile.oauth_users = [
        MagicMock(
            provider="feishu",
            open_id="ou_feishu",
            user_id="uid-1",
            uid="old",
            update_time=datetime(2024, 1, 1),
            create_time=datetime(2024, 1, 1),
        ),
        MagicMock(
            provider="dingtalk",
            open_id="ou_dingtalk",
            user_id="uid-1",
            uid="new",
            update_time=datetime(2026, 8, 1),
            create_time=datetime(2026, 8, 1),
        ),
    ]

    with patch.object(
        svc, "_resolve_recorder_profile", return_value=recorder_profile
    ), patch.object(
        svc, "_get_reporting_chain_leaders", return_value=[]
    ):
        recipients = svc.get_recipients_for_recorder(
            db_session,
            recorder_name="销售",
            recorder_id="recorder-1",
            recorder_profile=recorder_profile,
        )

    assert "feishu" not in recipients
    assert recipients["dingtalk"] == [
        {
            "open_id": "ou_dingtalk",
            "name": "销售",
            "type": "recorder",
            "department": "华东",
            "receive_id_type": "open_id",
            "platform": "dingtalk",
        }
    ]

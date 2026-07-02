"""拜访卡片推送：仅向 is_active 的 user profile 推送。"""

from unittest.mock import MagicMock, patch

from app.services.platform_notification_service import PlatformNotificationService
from app.services.visit_record_cc_resolver import resolve_visit_record_cc_recipients


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
    recorder_profile.oauth_user = MagicMock(provider="feishu", open_id="ou_recorder", user_id="uid-1")

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


def test_resolve_visit_record_cc_recipients_skips_inactive_executive_admin():
    db_session = MagicMock()

    def _permission_users(_permission):
        return [
            {
                "platform": "feishu",
                "open_id": "ou_active_exec",
                "name": "在职高管",
                "department": "公司",
            },
            {
                "platform": "feishu",
                "open_id": "ou_inactive_exec",
                "name": "停用高管",
                "department": "公司",
            },
        ]

    with patch(
        "app.services.visit_record_cc_resolver.user_profile_repo.get_active_open_ids",
        return_value={"ou_active_exec"},
    ):
        recipients = resolve_visit_record_cc_recipients(
            db_session,
            recorder_user_id=None,
            get_card_permission_receivers=_permission_users,
        )

    assert recipients == {
        "feishu": [
            {
                "open_id": "ou_active_exec",
                "name": "在职高管",
                "type": "executive_admin",
                "cc_scope": "global",
                "department": "公司",
                "receive_id_type": "open_id",
                "platform": "feishu",
            }
        ]
    }

"""拜访推送：录入人 profile 复用与协同人批量查询。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.services.platform_notification_service import PlatformNotificationService


RECORDER_USER_ID = UUID("11111111-1111-1111-1111-111111111111")


def _mock_recorder_profile():
    profile = MagicMock()
    profile.user_id = RECORDER_USER_ID
    profile.name = "销售甲"
    profile.department = "华东"
    oauth_account = MagicMock()
    oauth_account.provider = "feishu"
    oauth_account.open_id = "ou_recorder"
    oauth_account.user_id = RECORDER_USER_ID
    profile.oauth_user = oauth_account
    return profile


@patch("app.services.platform_notification_service.resolve_visit_record_cc_recipients", return_value={})
@patch.object(PlatformNotificationService, "_get_collaborative_participants_recipients", return_value={})
@patch.object(PlatformNotificationService, "_get_group_chats_by_department", return_value=[])
@patch.object(PlatformNotificationService, "get_recipients_for_recorder", return_value={})
@patch.object(PlatformNotificationService, "_resolve_recorder_profile")
def test_collect_visit_record_resolves_recorder_profile_once(
    mock_resolve_profile,
    mock_get_recorder,
    _mock_groups,
    _mock_collab,
    _mock_cc,
):
    recorder_profile = _mock_recorder_profile()
    mock_resolve_profile.return_value = recorder_profile
    svc = PlatformNotificationService()

    svc._collect_visit_record_recipients_and_groups(
        MagicMock(),
        recorder_name="销售甲",
        recorder_id="recorder-1",
        visit_record={},
    )

    mock_resolve_profile.assert_called_once()
    mock_get_recorder.assert_called_once()
    _, kwargs = mock_get_recorder.call_args
    assert kwargs.get("recorder_profile") is recorder_profile


@patch("app.services.platform_notification_service.user_profile_repo.get_by_oauth_user_ids")
def test_collaborative_participants_batch_loads_profiles(mock_get_by_oauth_user_ids):
    svc = PlatformNotificationService()
    profile = MagicMock()
    oauth_user = MagicMock()
    oauth_user.provider = "feishu"
    oauth_user.open_id = "ou_collab"
    profile.oauth_user = oauth_user
    profile.oauth_user_id = "ask_1"
    mock_get_by_oauth_user_ids.return_value = [profile]

    result = svc._get_collaborative_participants_recipients(
        MagicMock(),
        {
            "collaborative_participants": [
                {"name": "协同人A", "ask_id": "ask_1"},
                {"name": "协同人B", "ask_id": "ask_2"},
                {"name": "外部人", "ask_id": ""},
            ]
        },
    )

    mock_get_by_oauth_user_ids.assert_called_once()
    assert mock_get_by_oauth_user_ids.call_args[0][1] == ["ask_1", "ask_2"]
    assert result["feishu"][0]["open_id"] == "ou_collab"
    assert result["feishu"][0]["type"] == "collaborative_participant"

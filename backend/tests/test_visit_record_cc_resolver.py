"""visit_record_cc_resolver 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.services.visit_record_cc_resolver import resolve_visit_record_cc_recipients


RECORDER_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
CC_USER_ID = UUID("22222222-2222-2222-2222-222222222222")


def _mock_profile(user_id: UUID, *, name: str, open_id: str, platform: str = "feishu"):
    oauth_account = MagicMock()
    oauth_account.provider = platform
    oauth_account.open_id = open_id
    profile = MagicMock()
    profile.user_id = user_id
    profile.name = name
    profile.department = "销售部"
    profile.oauth_user = oauth_account
    return profile


def _mock_rule(rule_id: int, recipient_user_ids: list[UUID], *, scope_type: str = "user"):
    rule = MagicMock()
    rule.id = rule_id
    rule.scope_type = scope_type
    rule.recipients = [MagicMock(user_id=uid) for uid in recipient_user_ids]
    return rule


@patch("app.services.visit_record_cc_resolver.user_profile_repo.get_by_user_ids")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.merge_recipient_scopes")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_merges_multiple_rules_and_dedupes_recipients(
    mock_list_rules,
    mock_merge_scopes,
    mock_get_profiles,
):
    mock_list_rules.return_value = [
        _mock_rule(1, [CC_USER_ID]),
        _mock_rule(2, [CC_USER_ID]),
    ]
    mock_merge_scopes.return_value = [(CC_USER_ID, "user")]
    mock_get_profiles.return_value = [_mock_profile(CC_USER_ID, name="张三", open_id="ou_cc")]

    result = resolve_visit_record_cc_recipients(
        MagicMock(),
        recorder_user_id=RECORDER_USER_ID,
        get_card_permission_receivers=lambda _permission: [],
    )

    assert result == {
        "feishu": [
            {
                "open_id": "ou_cc",
                "name": "张三",
                "type": "configured_cc",
                "cc_scope": "user",
                "department": "销售部",
                "receive_id_type": "open_id",
                "platform": "feishu",
            }
        ]
    }


@patch("app.services.visit_record_cc_resolver.user_profile_repo.get_by_user_ids")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.merge_recipient_scopes")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_unions_rules_and_oauth_without_duplicate_open_id(
    mock_list_rules,
    mock_merge_scopes,
    mock_get_profiles,
):
    mock_list_rules.return_value = [_mock_rule(1, [CC_USER_ID])]
    mock_merge_scopes.return_value = [(CC_USER_ID, "user")]
    mock_get_profiles.return_value = [_mock_profile(CC_USER_ID, name="张三", open_id="ou_shared")]

    oauth_users = [
        {
            "name": "张三",
            "platform": "feishu",
            "open_id": "ou_shared",
            "raw": {"userId": str(CC_USER_ID)},
        },
        {
            "name": "李四",
            "platform": "feishu",
            "open_id": "ou_oauth",
            "raw": {},
        },
    ]

    result = resolve_visit_record_cc_recipients(
        MagicMock(),
        recorder_user_id=RECORDER_USER_ID,
        get_card_permission_receivers=lambda _permission: oauth_users,
    )

    assert len(result["feishu"]) == 2
    types = {item["type"] for item in result["feishu"]}
    open_ids = {item["open_id"] for item in result["feishu"]}
    assert types == {"configured_cc", "executive_admin"}
    assert open_ids == {"ou_shared", "ou_oauth"}


@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_oauth_only_when_recorder_user_id_missing(mock_list_rules):
    result = resolve_visit_record_cc_recipients(
        MagicMock(),
        recorder_user_id=None,
        get_card_permission_receivers=lambda _permission: [
            {
                "name": "高管",
                "platform": "feishu",
                "open_id": "ou_exec",
                "raw": {},
            }
        ],
    )

    mock_list_rules.assert_not_called()
    assert result["feishu"][0]["type"] == "executive_admin"
    assert result["feishu"][0]["cc_scope"] == "global"

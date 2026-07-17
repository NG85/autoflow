"""visit_record_cc_resolver 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.models.notification_cc_rule import (
    SCOPE_TYPE_DEPARTMENT,
    SCOPE_TYPE_GLOBAL,
    SCOPE_TYPE_USER,
)
from app.repositories.notification_cc_rule import notification_cc_rule_repo
from app.services.visit_record_cc_resolver import resolve_visit_record_cc_recipients


RECORDER_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
CC_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
GLOBAL_CC_USER_ID = UUID("33333333-3333-3333-3333-333333333333")
DEPT_CC_USER_ID = UUID("44444444-4444-4444-4444-444444444444")
RECORDER_DEPT_ID = "dept-sales-east"


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


@patch("app.services.visit_record_cc_resolver._resolve_recorder_department_id", return_value=None)
@patch("app.services.visit_record_cc_resolver.user_profile_repo.get_by_user_ids")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.merge_recipient_scopes")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_merges_multiple_rules_and_dedupes_recipients(
    mock_list_rules,
    mock_merge_scopes,
    mock_get_profiles,
    _mock_resolve_dept,
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


@patch("app.services.visit_record_cc_resolver._resolve_recorder_department_id", return_value=None)
@patch("app.services.visit_record_cc_resolver.user_profile_repo.get_by_user_ids")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.merge_recipient_scopes")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_includes_global_rules(
    mock_list_rules,
    mock_merge_scopes,
    mock_get_profiles,
    _mock_resolve_dept,
):
    mock_list_rules.return_value = [
        _mock_rule(1, [CC_USER_ID], scope_type="user"),
        _mock_rule(2, [GLOBAL_CC_USER_ID], scope_type="global"),
    ]
    mock_merge_scopes.return_value = [
        (CC_USER_ID, "user"),
        (GLOBAL_CC_USER_ID, "global"),
    ]
    mock_get_profiles.return_value = [
        _mock_profile(CC_USER_ID, name="张三", open_id="ou_cc"),
        _mock_profile(GLOBAL_CC_USER_ID, name="陈总", open_id="ou_global"),
    ]

    result = resolve_visit_record_cc_recipients(
        MagicMock(),
        recorder_user_id=RECORDER_USER_ID,
    )

    assert len(result["feishu"]) == 2
    by_open_id = {item["open_id"]: item for item in result["feishu"]}
    assert by_open_id["ou_cc"]["type"] == "configured_cc"
    assert by_open_id["ou_cc"]["cc_scope"] == "user"
    assert by_open_id["ou_global"]["type"] == "configured_cc"
    assert by_open_id["ou_global"]["cc_scope"] == "global"


@patch(
    "app.services.visit_record_cc_resolver._resolve_recorder_department_id",
    return_value=RECORDER_DEPT_ID,
)
@patch("app.services.visit_record_cc_resolver.user_profile_repo.get_by_user_ids")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.merge_recipient_scopes")
@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_includes_department_rules(
    mock_list_rules,
    mock_merge_scopes,
    mock_get_profiles,
    _mock_resolve_dept,
):
    mock_list_rules.return_value = [
        _mock_rule(1, [DEPT_CC_USER_ID], scope_type="department"),
        _mock_rule(2, [GLOBAL_CC_USER_ID], scope_type="global"),
    ]
    mock_merge_scopes.return_value = [
        (DEPT_CC_USER_ID, "department"),
        (GLOBAL_CC_USER_ID, "global"),
    ]
    mock_get_profiles.return_value = [
        _mock_profile(DEPT_CC_USER_ID, name="区域负责人", open_id="ou_dept"),
        _mock_profile(GLOBAL_CC_USER_ID, name="陈总", open_id="ou_global"),
    ]

    result = resolve_visit_record_cc_recipients(
        MagicMock(),
        recorder_user_id=RECORDER_USER_ID,
        recorder_department_id=RECORDER_DEPT_ID,
    )

    mock_list_rules.assert_called_once()
    assert mock_list_rules.call_args.kwargs["recorder_department_id"] == RECORDER_DEPT_ID
    by_open_id = {item["open_id"]: item for item in result["feishu"]}
    assert by_open_id["ou_dept"]["cc_scope"] == "department"
    assert by_open_id["ou_global"]["cc_scope"] == "global"


@patch("app.services.visit_record_cc_resolver.notification_cc_rule_repo.list_enabled_rules_for_recorder")
def test_resolve_returns_empty_when_recorder_user_id_missing(mock_list_rules):
    result = resolve_visit_record_cc_recipients(
        MagicMock(),
        recorder_user_id=None,
    )

    mock_list_rules.assert_not_called()
    assert result == {}


def test_merge_recipient_scopes_prefers_more_specific_scope():
    rules = [
        _mock_rule(1, [CC_USER_ID], scope_type=SCOPE_TYPE_GLOBAL),
        _mock_rule(2, [CC_USER_ID], scope_type=SCOPE_TYPE_DEPARTMENT),
        _mock_rule(3, [CC_USER_ID], scope_type=SCOPE_TYPE_USER),
        _mock_rule(4, [DEPT_CC_USER_ID], scope_type=SCOPE_TYPE_DEPARTMENT),
        _mock_rule(5, [GLOBAL_CC_USER_ID], scope_type=SCOPE_TYPE_GLOBAL),
    ]

    result = notification_cc_rule_repo.merge_recipient_scopes(rules)

    assert result == [
        (CC_USER_ID, SCOPE_TYPE_USER),
        (DEPT_CC_USER_ID, SCOPE_TYPE_DEPARTMENT),
        (GLOBAL_CC_USER_ID, SCOPE_TYPE_GLOBAL),
    ]

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.policies.review_session_access import (
    REVIEW_SESSION_VIEW_PERMISSION,
    WEEKLY_DECISION_DATA_SCOPE_ENTITY,
    WEEKLY_DECISION_VIEW_PERMISSION,
    ReviewSessionViewScope,
    resolve_review_session_view_scope,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
DEPT_ID = "dept-sales"
OTHER_DEPT_ID = "dept-hr"


def _allow_view_check() -> dict:
    return {"allowed": True, "function_allowed": True}


def _deny_view_check() -> dict:
    return {"allowed": False, "function_allowed": False}


def _no_global_scope() -> dict:
    return {"filters": [{"source": "org_team_sub", "enabled": True}]}


def _global_scope() -> dict:
    return {"filters": [{"source": "global", "enabled": True}]}


def _scope(
    *,
    has_viewer: bool = False,
    is_admin: bool = False,
    dept_id: str | None = DEPT_ID,
    subtree: tuple[str, ...] = (DEPT_ID, "dept-sales-east"),
) -> ReviewSessionViewScope:
    return ReviewSessionViewScope(
        has_viewer_permission=has_viewer,
        is_company_admin=is_admin,
        user_department_id=dept_id,
        subtree_department_ids=subtree if has_viewer and dept_id and not is_admin else (),
    )


def test_list_filter_mode_company_admin_viewer():
    scope = _scope(has_viewer=True, is_admin=True)
    assert scope.list_filter_mode == "company"


def test_list_filter_mode_department_viewer():
    scope = _scope(has_viewer=True, is_admin=False)
    assert scope.list_filter_mode == "department"


def test_list_filter_mode_viewer_without_department_sees_company():
    scope = _scope(has_viewer=True, is_admin=False, dept_id=None)
    assert scope.list_filter_mode == "company"
    assert scope.can_access_session_as_viewer(OTHER_DEPT_ID) is True


def test_list_filter_mode_regular_user():
    scope = _scope(has_viewer=False)
    assert scope.list_filter_mode == "attendee"


def test_can_access_session_as_viewer_department_subtree():
    scope = _scope(has_viewer=True, is_admin=False)
    assert scope.can_access_session_as_viewer(DEPT_ID) is True
    assert scope.can_access_session_as_viewer("dept-sales-east") is True
    assert scope.can_access_session_as_viewer(OTHER_DEPT_ID) is False


def test_can_access_session_as_viewer_company_admin():
    scope = _scope(has_viewer=True, is_admin=True)
    assert scope.can_access_session_as_viewer(OTHER_DEPT_ID) is True


def test_has_elevated_session_view_leader_wins():
    scope = _scope(has_viewer=False)
    assert scope.has_elevated_session_view(OTHER_DEPT_ID, is_leader=True) is True


def test_has_full_session_data_view_leader_sees_all():
    scope = _scope(has_viewer=False)
    assert scope.has_full_session_data_view(DEPT_ID, is_leader=True, is_attendee=True) is True


def test_has_full_session_data_view_non_leader_attendee_self_only_even_with_viewer():
    scope = _scope(has_viewer=True, is_admin=False)
    assert scope.has_elevated_session_view(DEPT_ID, is_leader=False) is True
    assert scope.has_full_session_data_view(DEPT_ID, is_leader=False, is_attendee=True) is False


def test_has_full_session_data_view_non_attendee_viewer_sees_all():
    scope = _scope(has_viewer=True, is_admin=False)
    assert scope.has_full_session_data_view(DEPT_ID, is_leader=False, is_attendee=False) is True
    assert scope.has_full_session_data_view(OTHER_DEPT_ID, is_leader=False, is_attendee=False) is False


@patch("app.policies.review_session_access.department_mirror_repo")
@patch("app.policies.review_session_access.user_department_relation_repo")
@patch("app.policies.review_session_access.user_profile_repo")
@patch("app.policies.review_session_access.oauth_client")
def test_resolve_review_session_view_scope_viewer_without_department(
    mock_oauth,
    mock_user_profile_repo,
    mock_user_dept_repo,
    mock_dept_mirror_repo,
):
    db_session = MagicMock()
    mock_oauth.check_function_permission.return_value = _allow_view_check()
    mock_oauth.get_data_scope.return_value = _no_global_scope()
    mock_user_profile_repo.get_crm_user_id_by_user_id.return_value = "crm-1"
    mock_user_dept_repo.get_primary_department_by_user_ids.return_value = {}

    scope = resolve_review_session_view_scope(db_session, USER_ID)

    assert scope.list_filter_mode == "company"
    assert scope.subtree_department_ids == ()
    mock_dept_mirror_repo.get_subtree_department_ids.assert_not_called()
    mock_oauth.check_function_permission.assert_called_once_with(
        user_id=USER_ID,
        permission=WEEKLY_DECISION_VIEW_PERMISSION,
    )
    mock_oauth.get_data_scope.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-1",
        entity=WEEKLY_DECISION_DATA_SCOPE_ENTITY,
    )
    assert REVIEW_SESSION_VIEW_PERMISSION == WEEKLY_DECISION_VIEW_PERMISSION
    assert WEEKLY_DECISION_VIEW_PERMISSION == "biz:weekly_decision:view"
    assert WEEKLY_DECISION_DATA_SCOPE_ENTITY == "biz_weekly_decision"


@patch("app.policies.review_session_access.department_mirror_repo")
@patch("app.policies.review_session_access.user_department_relation_repo")
@patch("app.policies.review_session_access.user_profile_repo")
@patch("app.policies.review_session_access.oauth_client")
def test_resolve_review_session_view_scope_department_viewer(
    mock_oauth,
    mock_user_profile_repo,
    mock_user_dept_repo,
    mock_dept_mirror_repo,
):
    db_session = MagicMock()
    mock_oauth.check_function_permission.return_value = _allow_view_check()
    mock_oauth.get_data_scope.return_value = _no_global_scope()
    mock_user_profile_repo.get_crm_user_id_by_user_id.return_value = "crm-1"
    mock_user_dept_repo.get_primary_department_by_user_ids.return_value = {
        str(USER_ID): DEPT_ID,
    }
    mock_dept_mirror_repo.get_subtree_department_ids.return_value = [DEPT_ID, "dept-sales-east"]

    scope = resolve_review_session_view_scope(db_session, USER_ID)

    assert scope.has_viewer_permission is True
    assert scope.is_company_admin is False
    assert scope.user_department_id == DEPT_ID
    assert scope.subtree_department_ids == (DEPT_ID, "dept-sales-east")
    assert scope.list_filter_mode == "department"


@patch("app.policies.review_session_access.department_mirror_repo")
@patch("app.policies.review_session_access.user_department_relation_repo")
@patch("app.policies.review_session_access.user_profile_repo")
@patch("app.policies.review_session_access.oauth_client")
def test_resolve_review_session_view_scope_global_data_scope(
    mock_oauth,
    mock_user_profile_repo,
    mock_user_dept_repo,
    mock_dept_mirror_repo,
):
    db_session = MagicMock()
    mock_oauth.check_function_permission.return_value = _allow_view_check()
    mock_oauth.get_data_scope.return_value = _global_scope()
    mock_user_profile_repo.get_crm_user_id_by_user_id.return_value = "crm-1"
    mock_user_dept_repo.get_primary_department_by_user_ids.return_value = {
        str(USER_ID): DEPT_ID,
    }

    scope = resolve_review_session_view_scope(db_session, USER_ID)

    assert scope.is_company_admin is True
    assert scope.list_filter_mode == "company"
    assert scope.subtree_department_ids == ()
    mock_dept_mirror_repo.get_subtree_department_ids.assert_not_called()


@patch("app.policies.review_session_access.user_department_relation_repo")
@patch("app.policies.review_session_access.user_profile_repo")
@patch("app.policies.review_session_access.oauth_client")
def test_resolve_review_session_view_scope_denied_viewer(
    mock_oauth,
    mock_user_profile_repo,
    mock_user_dept_repo,
):
    db_session = MagicMock()
    mock_oauth.check_function_permission.return_value = _deny_view_check()
    mock_oauth.get_data_scope.return_value = _no_global_scope()
    mock_user_profile_repo.get_crm_user_id_by_user_id.return_value = "crm-1"
    mock_user_dept_repo.get_primary_department_by_user_ids.return_value = {
        str(USER_ID): DEPT_ID,
    }

    scope = resolve_review_session_view_scope(db_session, USER_ID)

    assert scope.has_viewer_permission is False
    assert scope.list_filter_mode == "attendee"

"""WeeklyFollowupPermissionService 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.weekly_followup_permission_service import (
    WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY,
    WEEKLY_FOLLOWUP_VIEW_PERMISSION,
    WeeklyFollowupPermissionService,
)

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def _service() -> WeeklyFollowupPermissionService:
    return WeeklyFollowupPermissionService()


def test_gate_view_uses_weekly_followup_view_permission():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.weekly_followup_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": True, "allowed": True},
    ) as check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is True

    check.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        permission=WEEKLY_FOLLOWUP_VIEW_PERMISSION,
    )


def test_gate_view_denies_when_function_not_allowed():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.weekly_followup_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": False, "allowed": False},
    ):
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is False


def test_has_global_data_scope_true():
    service = _service()
    session = MagicMock()
    with patch.object(
        service,
        "get_data_scope",
        return_value={"entity": WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY, "filters": [{"source": "global", "enabled": True}]},
    ):
        assert service.has_global_data_scope(session, USER_ID) is True


def test_has_global_data_scope_false_without_global_filter():
    service = _service()
    session = MagicMock()
    with patch.object(
        service,
        "get_data_scope",
        return_value={
            "entity": WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY,
            "filters": [{"source": "self_owner", "crm_id": "crm-001"}],
        },
    ):
        assert service.has_global_data_scope(session, USER_ID) is False


def test_has_team_data_scope_true_for_org_scope():
    service = _service()
    session = MagicMock()
    with patch.object(
        service,
        "get_data_scope",
        return_value={
            "entity": WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY,
            "filters": [{"source": "org_scope", "enabled": True, "mode": "team_subordinates"}],
        },
    ):
        assert service.has_team_data_scope(session, USER_ID) is True


def test_has_team_data_scope_true_for_global():
    service = _service()
    session = MagicMock()
    with patch.object(
        service,
        "get_data_scope",
        return_value={"entity": WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY, "filters": [{"source": "global", "enabled": True}]},
    ):
        assert service.has_team_data_scope(session, USER_ID) is True


def test_has_team_data_scope_false_for_self_only():
    service = _service()
    session = MagicMock()
    with patch.object(
        service,
        "get_data_scope",
        return_value={
            "entity": WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY,
            "filters": [{"source": "self_owner", "enabled": True}],
        },
    ):
        assert service.has_team_data_scope(session, USER_ID) is False


def test_has_team_data_scope_skips_disabled_org_scope():
    service = _service()
    session = MagicMock()
    with patch.object(
        service,
        "get_data_scope",
        return_value={
            "entity": WEEKLY_FOLLOWUP_DATA_SCOPE_ENTITY,
            "filters": [{"source": "org_scope", "enabled": False}],
        },
    ):
        assert service.has_team_data_scope(session, USER_ID) is False

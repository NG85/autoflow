"""FollowUpPermissionService 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.follow_up_permission_service import FollowUpPermissionService

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def _service() -> FollowUpPermissionService:
    return FollowUpPermissionService()


def test_gate_view_allows_when_function_allowed():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.follow_up_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": True, "allowed": True},
    ) as check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is True

    check.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        permission="sales:follow_up:view",
    )


def test_gate_view_denies_when_function_not_allowed():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.follow_up_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": False, "allowed": False},
    ):
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is False


def test_build_list_perm_clause_orchestrates_scope_and_translate():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "follow_up",
        "merge": "OR",
        "filters": [{"source": "self_creator"}],
    }
    with patch.object(service, "get_data_scope", return_value=scope):
        with patch(
            "app.permissions.follow_up_permission_service.map_org_scope_from_filters",
            return_value=[],
        ):
            perm = service.build_list_perm_clause(session, USER_ID)

    assert "crm_sales_visit_records.recorder_id" in perm.sql
    assert perm.params["perm_user_id"] == str(USER_ID).replace("-", "")


def test_list_perm_where_returns_text_clause():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "follow_up",
        "merge": "OR",
        "filters": [{"source": "self_creator"}],
    }
    with patch.object(service, "get_data_scope", return_value=scope):
        with patch(
            "app.permissions.follow_up_permission_service.map_org_scope_from_filters",
            return_value=[],
        ):
            clause = service.list_perm_where(session, USER_ID)

    assert "crm_sales_visit_records.recorder_id" in str(clause)


def test_check_view_delegates_to_oauth_with_context():
    service = _service()
    session = MagicMock()
    record = MagicMock()
    record.record_id = "fu-001"
    record.recorder_id = USER_ID
    record.account_id = None
    record.opportunity_id = None
    record.partner_id = None

    with patch.object(service, "check_with_context", return_value=True) as check:
        with patch(
            "app.permissions.follow_up_permission_service.FollowUpContextBuilder"
        ) as builder_cls:
            builder_cls.return_value.build.return_value = {"recorder_id": str(USER_ID)}
            assert service.check_view(session, USER_ID, record) is True

    check.assert_called_once()
    assert check.call_args.kwargs["permission"] == "sales:follow_up:view"
    assert check.call_args.kwargs["resource_id"] == "fu-001"


def test_check_export_uses_function_allowed():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.follow_up_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": True, "allowed": True},
    ):
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.check_export(session, USER_ID) is True

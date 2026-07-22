"""CrmOpportunityPermissionService 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.crm_opportunity_permission_service import (
    CRM_OPPORTUNITY_VIEW_PERMISSION,
    CrmOpportunityPermissionService,
)

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def _service() -> CrmOpportunityPermissionService:
    return CrmOpportunityPermissionService()


def test_gate_view_uses_opportunity_view_permission():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_opportunity_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": True, "allowed": True},
    ) as check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is True

    check.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        permission=CRM_OPPORTUNITY_VIEW_PERMISSION,
    )


def test_gate_view_denies_when_function_not_allowed():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_opportunity_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": False, "allowed": False},
    ):
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is False


def test_build_list_perm_clause_uses_opportunity_data_scope():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "crm_opportunity",
        "merge": "OR",
        "filters": [{"source": "crm_data_authority", "crmId": "crm-001"}],
    }
    with patch.object(service, "get_data_scope", return_value=scope):
        perm = service.build_list_perm_clause(session, USER_ID)

    assert "crm_opportunities.unique_id" in perm.sql
    assert perm.params["perm_crm_id_0"] == "crm-001"
    assert perm.params["perm_entity_type"] == "crm_opportunity"

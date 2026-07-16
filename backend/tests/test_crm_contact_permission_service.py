"""CrmContactPermissionService 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.crm_contact_permission_service import (
    CRM_CONTACT_CREATE_PERMISSION,
    CRM_CONTACT_VIEW_PERMISSION,
    CrmContactPermissionService,
)

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
ACCOUNT_ID = "acc-001"


def _service() -> CrmContactPermissionService:
    return CrmContactPermissionService()


def test_check_create_on_account_calls_oauth_with_account_resource():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_contact_permission_service.oauth_client.check_permission",
        return_value={"allowed": True, "function_allowed": True, "data_allowed": True},
    ) as check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.check_create_on_account(session, USER_ID, ACCOUNT_ID) is True

    check.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        permission=CRM_CONTACT_CREATE_PERMISSION,
        resource={"type": "crm_account", "id": ACCOUNT_ID},
    )


def test_check_account_access_denies_when_oauth_denies():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_contact_permission_service.oauth_client.check_permission",
        return_value={"allowed": False, "function_allowed": True, "data_allowed": False},
    ):
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.check_account_access(session, USER_ID, ACCOUNT_ID) is False


def test_check_account_access_rejects_empty_customer_id():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_contact_permission_service.oauth_client.check_permission"
    ) as check:
        assert service.check_account_access(session, USER_ID, "  ") is False
    check.assert_not_called()


def test_gate_view_uses_contact_view_permission():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_contact_permission_service.oauth_client.check_permission",
        return_value={"function_allowed": True, "allowed": True},
    ) as check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.gate_view(session, USER_ID) is True

    check.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        permission=CRM_CONTACT_VIEW_PERMISSION,
    )


def test_build_list_perm_clause_uses_account_data_scope():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "crm_account",
        "merge": "OR",
        "filters": [{"source": "crm_data_authority", "crmId": "crm-001"}],
    }
    with patch.object(service, "get_account_data_scope", return_value=scope):
        perm = service.build_list_perm_clause(session, USER_ID)

    assert "local_contacts.customer_id" in perm.sql
    assert perm.params["perm_crm_id_0"] == "crm-001"


def test_check_view_calls_oauth_with_account_resource():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.crm_contact_permission_service.oauth_client.check_permission",
        return_value={"allowed": True, "function_allowed": True, "data_allowed": True},
    ) as check:
        with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
            assert service.check_view(session, USER_ID, customer_id=ACCOUNT_ID) is True

    check.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        permission=CRM_CONTACT_VIEW_PERMISSION,
        resource={"type": "crm_account", "id": ACCOUNT_ID},
    )

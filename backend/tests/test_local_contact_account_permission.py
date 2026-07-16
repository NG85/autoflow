"""local_contact_repo.search / check_account_permission OAuth 接入测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.crm_contact_permission_service import CRM_CONTACT_CREATE_PERMISSION
from app.repositories.local_contact import LocalContactRepo

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
ACCOUNT_ID = "acc-001"


def test_check_account_permission_uses_oauth():
    repo = LocalContactRepo()
    session = MagicMock()

    with patch(
        "app.repositories.local_contact.crm_contact_permission_service.check_account_access",
        return_value=True,
    ) as oauth_check:
        assert (
            repo.check_account_permission(
                session,
                USER_ID,
                ACCOUNT_ID,
                permission=CRM_CONTACT_CREATE_PERMISSION,
            )
            is True
        )

    oauth_check.assert_called_once_with(
        session,
        USER_ID,
        ACCOUNT_ID,
        permission=CRM_CONTACT_CREATE_PERMISSION,
    )


def test_search_applies_oauth_list_perm_where():
    repo = LocalContactRepo()
    session = MagicMock()
    from sqlalchemy import text

    perm_where = text("1=1")
    session.exec.return_value.one.return_value = 0
    session.exec.return_value.all.return_value = []

    with patch(
        "app.repositories.local_contact.crm_contact_permission_service.list_perm_where",
        return_value=perm_where,
    ) as list_perm:
        contacts, total = repo.search(session, USER_ID, customer_id=ACCOUNT_ID)

    list_perm.assert_called_once_with(session, USER_ID)
    assert contacts == []
    assert total == 0
    assert session.exec.call_count == 2

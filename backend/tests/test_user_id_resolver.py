"""user_id_resolver 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.permissions.user_id_resolver import (
    map_crm_user_ids_to_user_ids,
    map_org_scope_from_filters,
)

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
SUBORDINATE_ID = UUID("660e8400-e29b-41d4-a716-446655440001")


def test_map_crm_user_ids_to_user_ids_deduplicates():
    session = MagicMock()
    session.exec.return_value.all.return_value = [USER_ID, USER_ID]

    result = map_crm_user_ids_to_user_ids(session, ["crm-a", "crm-a"])

    assert result == [str(USER_ID)]


def test_map_org_scope_from_filters_explicit_crm_ids():
    session = MagicMock()
    session.exec.return_value.all.return_value = [USER_ID]
    filters = [
        {"source": "self_creator"},
        {"source": "org_scope", "crmUserIds": ["crm-mgr-001", "crm-sub-001"]},
    ]

    result = map_org_scope_from_filters(session, filters)

    assert result == [str(USER_ID)]
    session.exec.assert_called_once()


def test_map_org_scope_team_subordinates_expands_via_oauth():
    session = MagicMock()
    session.exec.return_value.all.return_value = [USER_ID]
    filters = [
        {
            "source": "org_scope",
            "mode": "team_subordinates",
            "crm_user_ids": ["crm-mgr-001"],
            "include_self": True,
        }
    ]

    with patch(
        "app.permissions.user_id_resolver._subordinate_user_ids_from_oauth",
        return_value=[str(USER_ID), str(SUBORDINATE_ID)],
    ):
        result = map_org_scope_from_filters(session, filters)

    assert result == [str(USER_ID), str(SUBORDINATE_ID)]


def test_map_org_scope_team_subordinates_excludes_self_when_disabled():
    session = MagicMock()
    session.exec.return_value.all.return_value = [USER_ID]
    filters = [
        {
            "source": "org_scope",
            "mode": "team_subordinates",
            "crm_user_ids": ["crm-mgr-001"],
            "include_self": False,
        }
    ]

    with patch(
        "app.permissions.user_id_resolver._subordinate_user_ids_from_oauth",
        return_value=[str(USER_ID), str(SUBORDINATE_ID)],
    ):
        result = map_org_scope_from_filters(session, filters)

    assert result == [str(SUBORDINATE_ID)]

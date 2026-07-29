"""ChatPermissionService 单元测试。"""

from unittest.mock import MagicMock, patch
from uuid import UUID

from app.models.chat import ChatType
from app.permissions.chat_permission_service import ChatPermissionService

USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
SUBORDINATE_ID = "660e8400-e29b-41d4-a716-446655440001"


def _service() -> ChatPermissionService:
    return ChatPermissionService()


def test_resolve_entity_maps_history_entities():
    service = _service()
    assert service.resolve_entity(ChatType.DEFAULT) == "enablement_sia_history"
    assert (
        service.resolve_entity(ChatType.CLIENT_VISIT_GUIDE)
        == "enablement_visit_guide_history"
    )
    # 接受原始字符串
    assert service.resolve_entity("default") == "enablement_sia_history"
    assert (
        service.resolve_entity("client_visit_guide")
        == "enablement_visit_guide_history"
    )


def test_resolve_entity_unmanaged_or_unknown_returns_none():
    service = _service()
    assert service.resolve_entity(None) is None
    assert service.resolve_entity(ChatType.REVIEW_SESSION) is None
    assert service.resolve_entity("not_a_chat_type") is None


def test_build_scope_unmanaged_type_denies_without_oauth_call():
    service = _service()
    session = MagicMock()
    with patch(
        "app.permissions.chat_permission_service.oauth_client.get_data_scope"
    ) as get_scope:
        result = service.build_scope(session, USER_ID, ChatType.REVIEW_SESSION)

    assert result.deny is True
    get_scope.assert_not_called()


def test_build_scope_self_only_maps_to_current_user():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "enablement_sia_history",
        "merge": "OR",
        "filters": [{"source": "self_only"}],
    }
    with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
        with patch(
            "app.permissions.chat_permission_service.oauth_client.get_data_scope",
            return_value=scope,
        ) as get_scope:
            with patch(
                "app.permissions.chat_permission_service.map_org_scope_from_filters",
                return_value=[],
            ):
                result = service.build_scope(session, USER_ID, ChatType.DEFAULT)

    get_scope.assert_called_once_with(
        user_id=USER_ID,
        crm_user_id="crm-001",
        entity="enablement_sia_history",
    )
    assert result.owner_user_ids == (str(USER_ID),)
    assert result.allow_all is False


def test_build_scope_global_allows_all():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "enablement_visit_guide_history",
        "merge": "OR",
        "filters": [{"source": "global", "enabled": True}],
    }
    with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
        with patch(
            "app.permissions.chat_permission_service.oauth_client.get_data_scope",
            return_value=scope,
        ):
            with patch(
                "app.permissions.chat_permission_service.map_org_scope_from_filters",
                return_value=[],
            ):
                result = service.build_scope(
                    session, USER_ID, ChatType.CLIENT_VISIT_GUIDE
                )

    assert result.allow_all is True


def test_build_scope_org_scope_expands_subordinates():
    service = _service()
    session = MagicMock()
    scope = {
        "entity": "enablement_sia_history",
        "merge": "OR",
        "filters": [{"source": "org_scope", "mode": "team_subordinates"}],
    }
    with patch.object(service, "resolve_crm_user_id", return_value="crm-mgr"):
        with patch(
            "app.permissions.chat_permission_service.oauth_client.get_data_scope",
            return_value=scope,
        ):
            with patch(
                "app.permissions.chat_permission_service.map_org_scope_from_filters",
                return_value=[str(USER_ID), SUBORDINATE_ID],
            ):
                result = service.build_scope(session, USER_ID, ChatType.DEFAULT)

    assert result.owner_user_ids == (str(USER_ID), SUBORDINATE_ID)


def test_build_scope_empty_filters_denies():
    service = _service()
    session = MagicMock()
    scope = {"entity": "enablement_sia_history", "merge": "OR", "filters": []}
    with patch.object(service, "resolve_crm_user_id", return_value="crm-001"):
        with patch(
            "app.permissions.chat_permission_service.oauth_client.get_data_scope",
            return_value=scope,
        ):
            with patch(
                "app.permissions.chat_permission_service.map_org_scope_from_filters",
                return_value=[],
            ):
                result = service.build_scope(session, USER_ID, ChatType.DEFAULT)

    assert result.deny is True

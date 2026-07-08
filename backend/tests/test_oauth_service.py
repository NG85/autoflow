"""OAuthClient fallbacks and transport."""

from unittest.mock import MagicMock, patch
from uuid import UUID

import requests

from app.services.oauth_service import OAuthClient


USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _client() -> OAuthClient:
    return OAuthClient(base_url="http://auth:8018", session=MagicMock())


def test_permission_query_success_and_cache():
    client = _client()
    mock_data = {
        "code": 0,
        "result": {"roles": [{"code": "admin"}], "permissions": ["crm:view"]},
    }
    with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
        first = client.query_user_roles_and_permissions(user_id=USER_ID)
        second = client.query_user_roles_and_permissions(user_id=USER_ID)

    assert first == {"roles": [{"code": "admin"}], "permissions": ["crm:view"]}
    assert second == first
    post.assert_called_once()


def test_permission_requests_include_bearer_when_token_configured():
    client = _client()
    mock_data = {"code": 0, "result": {"roles": [], "permissions": []}}
    with patch("app.services.oauth_service.settings") as mock_settings:
        mock_settings.OAUTH_PERMISSION_API_TOKEN = "service-token"
        with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
            client.query_user_roles_and_permissions(user_id=USER_ID)

    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer service-token"}


def test_permission_requests_omit_bearer_when_token_empty():
    client = _client()
    mock_data = {"code": 0, "result": {"roles": [], "permissions": []}}
    with patch("app.services.oauth_service.settings") as mock_settings:
        mock_settings.OAUTH_PERMISSION_API_TOKEN = ""
        with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
            client.query_user_roles_and_permissions(user_id=USER_ID)

    assert post.call_args.kwargs["headers"] is None


def test_permission_query_transport_failure_returns_empty():
    client = _client()
    with patch("app.services.oauth_service.post_json", return_value=None):
        result = client.query_user_roles_and_permissions(user_id=USER_ID)

    assert result == {"roles": [], "permissions": []}


def test_permission_query_failure_not_cached():
    client = _client()
    ok = {"code": 0, "result": {"roles": [], "permissions": ["a"]}}
    with patch("app.services.oauth_service.post_json", side_effect=[None, ok]) as post:
        first = client.query_user_roles_and_permissions(user_id=USER_ID)
        second = client.query_user_roles_and_permissions(user_id=USER_ID)

    assert first == {"roles": [], "permissions": []}
    assert second == {"roles": [], "permissions": ["a"]}
    assert post.call_count == 2


def test_departments_leaders_business_error_returns_empty_dict():
    client = _client()
    with patch(
        "app.services.oauth_service.post_json",
        return_value={"code": 1, "message": "error"},
    ):
        assert client.get_departments_with_leaders() == {}


def test_departments_leaders_transport_failure_returns_empty_dict():
    client = _client()
    with patch("app.services.oauth_service.post_json", return_value=None):
        assert client.get_departments_with_leaders() == {}


def test_reporting_chain_transport_failure_returns_empty_list():
    client = _client()
    with patch("app.services.oauth_service.post_json", return_value=None):
        assert client.get_reporting_chain_leaders(base_user_id="u1") == []


def test_subordinate_chain_transport_failure_returns_empty_dict():
    client = _client()
    with patch("app.services.oauth_service.post_json", return_value=None):
        assert client.get_subordinate_chain(user_id=USER_ID) == {}


def test_subordinate_chain_success_and_cache():
    client = _client()
    mock_data = {
        "code": 0,
        "result": {
            "userId": str(USER_ID),
            "subordinates": [{"userId": "sub-1", "crmUserId": "crm-sub", "name": "Sub"}],
        },
    }
    with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
        first = client.get_subordinate_chain(user_id=USER_ID)
        second = client.get_subordinate_chain(user_id=USER_ID)

    assert len(first.get("subordinates") or []) == 1
    assert second == first
    post.assert_called_once()


def test_check_function_permission_allowed():
    client = _client()
    mock_data = {
        "code": 0,
        "result": {
            "allowed": True,
            "functionAllowed": True,
            "dataAllowed": True,
            "effect": "ALLOW",
            "requiresAudit": True,
        },
    }
    with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
        result = client.check_function_permission(
            user_id=USER_ID,
            permission="sales:follow_up:edit",
        )

    assert result["allowed"] is True
    assert result["requires_audit"] is True
    post.assert_called_once()
    assert post.call_args.kwargs["path"] == "/permission/check"
    assert post.call_args.kwargs["json_body"] == {
        "user_id": str(USER_ID),
        "permission": "sales:follow_up:edit",
    }


def test_check_function_permission_denied_on_transport_failure():
    client = _client()
    with patch("app.services.oauth_service.post_json", return_value=None):
        result = client.check_function_permission(
            user_id=USER_ID,
            permission="sales:follow_up:edit",
        )
    assert result["allowed"] is False
    assert result["effect"] == "DENY"


def test_check_permission_with_resource_and_context():
    client = _client()
    mock_data = {
        "code": 0,
        "result": {
            "allowed": True,
            "functionAllowed": True,
            "dataAllowed": True,
            "effect": "ALLOW",
        },
    }
    with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
        result = client.check_permission(
            user_id=USER_ID,
            crm_user_id="crm-001",
            permission="sales:follow_up:edit",
            resource={"type": "follow_up", "id": "fu-001"},
            context={"recorder_id": str(USER_ID), "is_collaborator": False},
        )

    assert result["allowed"] is True
    assert post.call_args.kwargs["json_body"] == {
        "user_id": str(USER_ID),
        "crm_user_id": "crm-001",
        "permission": "sales:follow_up:edit",
        "resource": {"type": "follow_up", "id": "fu-001"},
        "context": {"recorder_id": str(USER_ID), "is_collaborator": False},
    }


def test_get_data_scope_success_and_cache():
    client = _client()
    mock_data = {
        "code": 0,
        "result": {
            "entity": "follow_up",
            "merge": "OR",
            "filters": [{"source": "self_creator"}],
        },
    }
    with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
        first = client.get_data_scope(user_id=USER_ID, entity="follow_up", crm_user_id="crm-001")
        second = client.get_data_scope(user_id=USER_ID, entity="follow_up", crm_user_id="crm-001")

    assert first["entity"] == "follow_up"
    assert first["filters"][0]["source"] == "self_creator"
    assert second == first
    post.assert_called_once()
    assert post.call_args.kwargs["path"] == "/permission/data-scope"


def test_get_data_scope_transport_failure_returns_empty_filters():
    client = _client()
    with patch("app.services.oauth_service.post_json", return_value=None):
        result = client.get_data_scope(user_id=USER_ID, entity="follow_up")

    assert result == {"entity": "follow_up", "merge": "OR", "filters": []}


def test_batch_check_permissions_success():
    client = _client()
    mock_data = {
        "code": 0,
        "result": {
            "results": [
                {
                    "permission": "sales:follow_up:edit",
                    "allowed": True,
                    "functionAllowed": True,
                    "dataAllowed": True,
                    "effect": "ALLOW",
                },
                {
                    "permission": "sales:follow_up:delete",
                    "allowed": False,
                    "functionAllowed": False,
                    "dataAllowed": False,
                    "effect": "DENY",
                },
            ]
        },
    }
    checks = [
        {"permission": "sales:follow_up:edit", "resource": {"type": "follow_up", "id": "fu-1"}},
        {"permission": "sales:follow_up:delete", "resource": {"type": "follow_up", "id": "fu-1"}},
    ]
    with patch("app.services.oauth_service.post_json", return_value=mock_data) as post:
        results = client.batch_check_permissions(user_id=USER_ID, crm_user_id="crm-001", checks=checks)

    assert results[0]["allowed"] is True
    assert results[1]["allowed"] is False
    assert post.call_args.kwargs["path"] == "/permission/batch-check"


def test_batch_check_permissions_transport_failure_denies_all():
    client = _client()
    checks = [{"permission": "sales:follow_up:edit"}]
    with patch("app.services.oauth_service.post_json", return_value=None):
        results = client.batch_check_permissions(user_id=USER_ID, checks=checks)

    assert len(results) == 1
    assert results[0]["allowed"] is False


def test_post_json_retries_on_transport_error():
    from app.services.oauth_http import post_json

    session = MagicMock()
    ok_resp = MagicMock()
    ok_resp.ok = True
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"code": 0}

    session.post.side_effect = [
        requests.ConnectionError("refused"),
        ok_resp,
    ]

    with patch("app.services.oauth_http.settings") as mock_settings:
        mock_settings.OAUTH_CLIENT_DEFAULT_TIMEOUT_SECONDS = 5.0
        mock_settings.OAUTH_CLIENT_RETRY_ATTEMPTS = 1
        mock_settings.OAUTH_CLIENT_RETRY_BACKOFF_SECONDS = 0
        with patch("app.services.oauth_http.time.sleep"):
            data = post_json(
                session,
                base_url="http://auth:8018",
                operation="test",
                path="/permission/query",
                json_body={"user_id": "x"},
            )

    assert data == {"code": 0}
    assert session.post.call_count == 2

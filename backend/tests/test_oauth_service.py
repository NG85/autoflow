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

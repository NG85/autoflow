"""tenant_access_token 失效时自动清缓存并重试一次。"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.services.platform_notification_service import (
    PlatformNotificationService,
    _is_invalid_tenant_token_error,
)


def _http_error_with_body(body: str, status_code: int = 400) -> requests.HTTPError:
    response = MagicMock()
    response.text = body
    response.status_code = status_code
    response.json.return_value = __import__("json").loads(body)
    exc = requests.HTTPError(response=response)
    exc.response = response
    return exc


def test_is_invalid_tenant_token_error_feishu_code():
    exc = _http_error_with_body(
        '{"code":99991663,"msg":"Invalid access token for authorization."}'
    )
    assert _is_invalid_tenant_token_error(exc) is True


def test_is_invalid_tenant_token_error_other_error():
    exc = _http_error_with_body('{"code":12345,"msg":"template not found"}')
    assert _is_invalid_tenant_token_error(exc) is False


def test_send_message_retries_once_on_invalid_token():
    svc = PlatformNotificationService()
    token_error = _http_error_with_body(
        '{"code":99991663,"msg":"Invalid access token for authorization."}'
    )

    with patch.object(
        svc, "_dispatch_platform_message", side_effect=[token_error, {"code": 0}]
    ) as mock_dispatch, patch.object(
        svc, "_invalidate_tenant_access_token"
    ) as mock_invalidate, patch.object(
        svc, "_get_tenant_access_token", return_value="fresh_token"
    ) as mock_get_token:
        result = svc._send_message("ou_1", "stale_token", {"text": "hi"}, "feishu")

    assert result == {"code": 0}
    mock_invalidate.assert_called_once_with("feishu")
    mock_get_token.assert_called_once_with("feishu", force_refresh=True)
    assert mock_dispatch.call_count == 2
    assert mock_dispatch.call_args_list[0].args[1] == "stale_token"
    assert mock_dispatch.call_args_list[1].args[1] == "fresh_token"


def test_send_message_does_not_retry_on_non_token_error():
    svc = PlatformNotificationService()
    other_error = _http_error_with_body('{"code":230001,"msg":"template not found"}')

    with patch.object(
        svc, "_dispatch_platform_message", side_effect=other_error
    ) as mock_dispatch, patch.object(svc, "_invalidate_tenant_access_token") as mock_invalidate:
        with pytest.raises(requests.HTTPError):
            svc._send_message("ou_1", "token", {"text": "hi"}, "feishu")

    mock_invalidate.assert_not_called()
    assert mock_dispatch.call_count == 1

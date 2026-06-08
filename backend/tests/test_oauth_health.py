"""OAuth health probe."""

from unittest.mock import MagicMock, patch

import requests

from app.services.oauth_health import probe_oauth_service


def test_probe_oauth_ok():
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200

    with patch("app.services.oauth_health.requests.get", return_value=mock_resp):
        with patch("app.services.oauth_health.settings") as mock_settings:
            mock_settings.OAUTH_BASE_URL = "http://auth:8018"
            mock_settings.OAUTH_HEALTH_PROBE_ENABLED = True
            mock_settings.OAUTH_HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
            result = probe_oauth_service()

    assert result["status"] == "ok"
    assert result["reachable"] is True
    assert result["health_url"] == "http://auth:8018/health"


def test_probe_oauth_degraded_on_connection_error():
    with patch(
        "app.services.oauth_health.requests.get",
        side_effect=requests.ConnectionError("connection refused"),
    ):
        with patch("app.services.oauth_health.settings") as mock_settings:
            mock_settings.OAUTH_BASE_URL = "http://auth:8018"
            mock_settings.OAUTH_HEALTH_PROBE_ENABLED = True
            mock_settings.OAUTH_HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
            result = probe_oauth_service()

    assert result["status"] == "degraded"
    assert result["reachable"] is False
    assert result["error"]


def test_probe_disabled():
    with patch("app.services.oauth_health.settings") as mock_settings:
        mock_settings.OAUTH_BASE_URL = "http://auth:8018"
        mock_settings.OAUTH_HEALTH_PROBE_ENABLED = False
        mock_settings.OAUTH_HEALTH_PROBE_TIMEOUT_SECONDS = 3.0
        result = probe_oauth_service()

    assert result["status"] == "disabled"

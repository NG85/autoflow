import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def env():
    print("Loading environment variables")
    load_dotenv()


@pytest.fixture(autouse=True)
def auth_test_defaults(monkeypatch):
    """Keep auth unit tests independent of developer .env (oauth shadow)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_LEGACY_OAUTH_SHADOW_ENABLED", False)


@pytest.fixture
def client():
    """FastAPI test client for API route tests."""
    from main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

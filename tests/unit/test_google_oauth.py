import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock

from chloe.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app(), follow_redirects=False)


def test_google_start_redirects(client):
    import chloe.admin.api as api_mod
    from chloe.config import Settings

    fake_settings = Settings()
    fake_settings.google_client_id = "google-client-id"
    fake_settings.google_redirect_uri = "http://localhost/cb"

    with patch.object(api_mod, "get_settings", return_value=fake_settings):
        response = client.get("/admin/oauth/google/start")

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "gmail.readonly" in location.replace("%20", " ")


def test_google_start_missing_client_id_returns_500(client):
    import chloe.admin.api as api_mod
    from chloe.config import Settings

    fake_settings = Settings()
    fake_settings.google_client_id = ""

    with patch.object(api_mod, "get_settings", return_value=fake_settings):
        response = client.get("/admin/oauth/google/start")

    assert response.status_code == 500
    assert "GOOGLE_CLIENT_ID" in response.text


def test_google_callback_no_code_400(client):
    response = client.get("/admin/oauth/google/callback")
    assert response.status_code == 400
    assert "No code" in response.text


def test_google_callback_error_param(client):
    response = client.get("/admin/oauth/google/callback?error=access_denied")
    assert response.status_code == 400
    assert "access_denied" in response.text


def test_google_callback_success(client):
    import chloe.admin.api as api_mod
    from chloe.config import Settings

    fake_settings = Settings()
    fake_settings.google_client_id = "gcid"
    fake_settings.google_client_secret = MagicMock()
    fake_settings.google_client_secret.get_secret_value.return_value = "gsecret"
    fake_settings.google_redirect_uri = "http://localhost/gcb"

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "gtok123", "refresh_token": "gref456"}

    userinfo_response = MagicMock()
    userinfo_response.status_code = 200
    userinfo_response.json.return_value = {"name": "Teo"}

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=token_response)
    mock_client.get = AsyncMock(return_value=userinfo_response)

    with (
        patch.object(api_mod, "get_settings", return_value=fake_settings),
        patch("chloe.admin.api.httpx.AsyncClient", return_value=mock_client),
        patch("chloe.admin.api.store_token") as mock_store,
    ):
        response = client.get("/admin/oauth/google/callback?code=authcode123")

    assert response.status_code == 200
    assert "Teo" in response.text
    assert "Google Connected" in response.text
    mock_store.assert_called_once_with(
        "google", {"access_token": "gtok123", "refresh_token": "gref456"}
    )

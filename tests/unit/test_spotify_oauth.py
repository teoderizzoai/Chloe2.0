import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock

from chloe.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app(), follow_redirects=False)


def test_spotify_start_redirects_to_auth_url(client, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test-secret")

    import chloe.admin.api as api_mod
    from chloe.config import Settings

    fake_settings = Settings()
    fake_settings.spotify_client_id = "test-client-id"

    with patch.object(api_mod, "get_settings", return_value=fake_settings):
        response = client.get("/admin/oauth/spotify/start")

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.spotify.com" in location
    assert "test-client-id" in location


def test_spotify_start_missing_client_id_returns_500(client):
    import chloe.admin.api as api_mod
    from chloe.config import Settings

    fake_settings = Settings()
    fake_settings.spotify_client_id = ""

    with patch.object(api_mod, "get_settings", return_value=fake_settings):
        response = client.get("/admin/oauth/spotify/start")

    assert response.status_code == 500
    assert "SPOTIFY_CLIENT_ID" in response.text


def test_spotify_callback_no_code_returns_error(client):
    response = client.get("/admin/oauth/spotify/callback")
    assert response.status_code == 400
    assert "No code" in response.text


def test_spotify_callback_with_error_param(client):
    response = client.get("/admin/oauth/spotify/callback?error=access_denied")
    assert response.status_code == 400
    assert "access_denied" in response.text


def test_spotify_callback_success(client):
    import chloe.admin.api as api_mod
    from chloe.config import Settings

    fake_settings = Settings()
    fake_settings.spotify_client_id = "cid"
    fake_settings.spotify_client_secret = MagicMock()
    fake_settings.spotify_client_secret.get_secret_value.return_value = "csecret"
    fake_settings.spotify_redirect_uri = "http://localhost/cb"

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "tok123", "token_type": "Bearer"}

    profile_response = MagicMock()
    profile_response.status_code = 200
    profile_response.json.return_value = {"display_name": "Teo"}

    async def fake_post(*args, **kwargs):
        return token_response

    async def fake_get(*args, **kwargs):
        return profile_response

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=token_response)
    mock_client.get = AsyncMock(return_value=profile_response)

    with (
        patch.object(api_mod, "get_settings", return_value=fake_settings),
        patch("chloe.admin.api.httpx.AsyncClient", return_value=mock_client),
        patch("chloe.admin.api.store_token") as mock_store,
    ):
        response = client.get("/admin/oauth/spotify/callback?code=authcode123")

    assert response.status_code == 200
    assert "Teo" in response.text
    assert "Spotify Connected" in response.text
    mock_store.assert_called_once_with("spotify", {"access_token": "tok123", "token_type": "Bearer"})

# B-02 · Admin OAuth flow for Spotify

## Overview

Add two admin endpoints: `GET /admin/oauth/spotify/start` → redirect to Spotify authorization URL; `GET /admin/oauth/spotify/callback` → exchange code, store encrypted token, display success page showing the Spotify display name.

## Context

Spotify requires user-delegated OAuth. The admin flow lets Teo (the only user) authenticate once via a browser. The access token is then stored encrypted in `kv` and used by all Spotify tool verbs. Without this flow, `tools/spotify.py` has no valid token.

## Implementation

```python
# In chloe/admin/api.py (add to existing admin router)

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, HTMLResponse
import httpx, base64, json
from chloe.config import get_settings
from chloe.state.oauth_tokens import store as store_token
from chloe.observability.logging import get_logger

log = get_logger("admin.oauth")

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SCOPES = " ".join([
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-recently-played",
    "user-library-modify",
    "playlist-modify-private",
    "playlist-modify-public",
])

@admin_router.get("/oauth/spotify/start")
async def spotify_oauth_start():
    s = get_settings()
    if not s.spotify_client_id:
        return HTMLResponse("<h1>Error</h1><p>SPOTIFY_CLIENT_ID not configured.</p>", status_code=500)
    
    params = {
        "client_id": s.spotify_client_id,
        "response_type": "code",
        "redirect_uri": s.spotify_redirect_uri,
        "scope": SPOTIFY_SCOPES,
        "state": "chloe-oauth-csrf",   # simple CSRF token; improve in production
    }
    auth_url = SPOTIFY_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(auth_url)

@admin_router.get("/oauth/spotify/callback")
async def spotify_oauth_callback(code: str = None, error: str = None, state: str = None):
    if error:
        return HTMLResponse(f"<h1>OAuth Error</h1><p>{error}</p>", status_code=400)
    if not code:
        return HTMLResponse("<h1>Error</h1><p>No code received.</p>", status_code=400)
    
    s = get_settings()
    creds = base64.b64encode(
        f"{s.spotify_client_id}:{s.spotify_client_secret.get_secret_value()}".encode()
    ).decode()
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.spotify_redirect_uri,
            },
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        )
    
    if resp.status_code != 200:
        log.error("spotify_token_exchange_failed", status=resp.status_code)
        return HTMLResponse(f"<h1>Token Error</h1><p>{resp.text}</p>", status_code=500)
    
    token_data = resp.json()
    store_token("spotify", token_data)
    
    # Fetch user profile to show display name
    async with httpx.AsyncClient() as client:
        profile_resp = await client.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
    display_name = profile_resp.json().get("display_name", "Unknown") if profile_resp.status_code == 200 else "Unknown"
    
    log.info("spotify_oauth_complete", display_name=display_name)
    return HTMLResponse(f"""
        <h1>Spotify Connected</h1>
        <p>Connected as <strong>{display_name}</strong></p>
        <p><a href="/admin/">Back to admin</a></p>
    """)
```

## Dependencies

- B-01 (`store_token` for encrypted storage).
- F-02 (config for Spotify credentials).
- F-11 (admin router already mounted).

## Testing

### Unit tests — `tests/unit/test_spotify_oauth.py`

```python
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
    response = client.get("/admin/oauth/spotify/start")
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.spotify.com" in location
    assert "test-client-id" in location

def test_spotify_callback_no_code_returns_error(client):
    response = client.get("/admin/oauth/spotify/callback")
    assert response.status_code == 400

def test_spotify_callback_with_error_param(client):
    response = client.get("/admin/oauth/spotify/callback?error=access_denied")
    assert response.status_code == 400
    assert "access_denied" in response.text
```

### Manual UAT checklist

- [ ] Visit `http://localhost:8000/admin/oauth/spotify/start` in browser
- [ ] Spotify login page appears
- [ ] Complete auth with Teo's Spotify account
- [ ] Redirected to `/admin/oauth/spotify/callback`
- [ ] Page shows "Connected as {display_name}"
- [ ] `kv` table has an `oauth_token:spotify` entry (encrypted blob)
- [ ] `load("spotify")` returns a dict with `access_token` field

## Acceptance criteria

- Manual UAT: visit start URL in browser, complete Spotify auth, see success page, `kv` has encrypted token.
- Unit test: `/admin/oauth/spotify/start` redirects to Spotify with correct client_id.
- No token values appear in logs.

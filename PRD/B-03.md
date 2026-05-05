# B-03 · Admin OAuth flow for Google (Gmail + Calendar)

## Overview

Add `GET /admin/oauth/google/start` → redirect to Google authorization URL; `GET /admin/oauth/google/callback` → exchange code, store token as `"google"`, display success page. Single OAuth app covers both Gmail and Calendar scopes.

## Context

Both Gmail (B-05) and Calendar (B-06) use the same Google OAuth token stored under the key `"google"`. The flow is identical to Spotify (B-02) but uses Google's PKCE-compatible OAuth 2.0 endpoints. Scopes requested: `gmail.readonly`, `gmail.modify`, `calendar.events`, and `openid profile email` (for the display name).

## OAuth scopes

```python
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]
```

Note: `gmail.send` scope is NOT requested here — it is added in Phase G (G-01) when `send_reply` goes live.

## Implementation

```python
# In chloe/admin/api.py (add to existing admin router)

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO  = "https://www.googleapis.com/oauth2/v3/userinfo"

@admin_router.get("/oauth/google/start")
async def google_oauth_start():
    s = get_settings()
    if not s.google_client_id:
        return HTMLResponse("<h1>Error</h1><p>GOOGLE_CLIENT_ID not configured.</p>", status_code=500)
    
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",   # get refresh_token
        "prompt": "consent",        # force re-consent so refresh_token is always returned
        "state": "chloe-google-csrf",
    }
    url = GOOGLE_AUTH_URL + "?" + "&".join(f"{k}={v.replace(' ','%20')}" for k, v in params.items())
    return RedirectResponse(url)

@admin_router.get("/oauth/google/callback")
async def google_oauth_callback(code: str = None, error: str = None):
    if error:
        return HTMLResponse(f"<h1>OAuth Error</h1><p>{error}</p>", status_code=400)
    if not code:
        return HTMLResponse("<h1>Error</h1><p>No code.</p>", status_code=400)
    
    s = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.google_redirect_uri,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret.get_secret_value(),
            },
        )
    
    if resp.status_code != 200:
        return HTMLResponse(f"<h1>Token Error</h1><p>{resp.text}</p>", status_code=500)
    
    token_data = resp.json()
    store_token("google", token_data)
    
    # Get user info for display
    async with httpx.AsyncClient() as client:
        ui_resp = await client.get(
            GOOGLE_USERINFO,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
    name = ui_resp.json().get("name", "Unknown") if ui_resp.status_code == 200 else "Unknown"
    
    log.info("google_oauth_complete", name=name)
    return HTMLResponse(f"""
        <h1>Google Connected</h1>
        <p>Connected as <strong>{name}</strong></p>
        <p>Scopes: Gmail (readonly + modify), Calendar Events</p>
        <p><a href="/admin/">Back to admin</a></p>
    """)
```

## Dependencies

- B-01 (`store_token`).
- F-02 (config for Google credentials).
- F-11 (admin router).

## Testing

### Unit tests — `tests/unit/test_google_oauth.py`

```python
import pytest
from fastapi.testclient import TestClient
from chloe.app import create_app

@pytest.fixture
def client():
    return TestClient(create_app(), follow_redirects=False)

def test_google_start_redirects(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-secret")
    response = client.get("/admin/oauth/google/start")
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "gmail.readonly" in location.replace("%20", " ")

def test_google_callback_no_code_400(client):
    response = client.get("/admin/oauth/google/callback")
    assert response.status_code == 400

def test_google_callback_error_param(client):
    response = client.get("/admin/oauth/google/callback?error=access_denied")
    assert response.status_code == 400
```

### Manual UAT checklist

- [ ] Visit `/admin/oauth/google/start` in browser
- [ ] Google consent screen appears with Gmail + Calendar scopes
- [ ] Complete auth with Teo's Google account
- [ ] Page shows "Connected as {name}"
- [ ] `load("google")` returns dict with `access_token` and `refresh_token`
- [ ] Token usable for both Gmail read and Calendar read (tested in B-05/B-06)

## Acceptance criteria

- Manual UAT: Google auth completes; page shows name; `load("google")` is a valid dict.
- Token usable for both Gmail and Calendar reads (confirmed when B-05 and B-06 integration tests pass with `@pytest.mark.live`).
- `gmail.send` scope is NOT in the requested scopes list (guarded until Phase G).

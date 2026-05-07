import base64

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from chloe.actions import audit
from chloe.config import get_settings
from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import store as store_token

admin_router = APIRouter()

log = get_logger("admin.oauth")


ADMIN_INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Chloe — Admin</title>
  <meta charset="utf-8">
  <style>
    body { font-family: sans-serif; background: #f5f5f5; padding: 40px; }
    h1 { color: #333; }
    .section { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    a { color: #0066cc; text-decoration: none; padding: 10px 20px; display: inline-block; margin: 5px 0; border-radius: 4px; border: 1px solid #ddd; }
    a:hover { background: #f0f0f0; }
  </style>
</head>
<body>
  <h1>Chloe 2.0 Admin</h1>
  
  <div class="section">
    <h2>Monitoring</h2>
    <a href="/admin/audit/ui">Audit Feed</a>
    <a href="/admin/audit">Audit API (JSON)</a>
    <a href="/admin/cache/status">Cache Status</a>
    <a href="/metrics">Prometheus Metrics</a>
  </div>
  
  <div class="section">
    <h2>Integration Setup</h2>
    <a href="/admin/oauth/google/start">Connect Google</a>
    <a href="/admin/oauth/spotify/start">Connect Spotify</a>
  </div>
  
  <div class="section">
    <h2>Maintenance</h2>
    <button onclick="resetCache()">Reset Cache</button>
  </div>
  
  <script>
    async function resetCache() {
      const resp = await fetch('/admin/cache/reset', { method: 'POST' });
      const data = await resp.json();
      alert('Cache reset: ' + data.cache_name);
    }
  </script>
</body>
</html>
"""


@admin_router.get("/")
async def admin_index():
    return HTMLResponse(ADMIN_INDEX_HTML)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]

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

AUDIT_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Chloe — Audit Feed</title>
  <meta charset="utf-8">
  <style>
    body { font-family: monospace; background: #111; color: #eee; padding: 20px; }
    h1 { color: #9f9; }
    table { border-collapse: collapse; width: 100%; }
    th { text-align: left; background: #222; padding: 8px; border-bottom: 1px solid #444; }
    td { padding: 6px 8px; border-bottom: 1px solid #333; font-size: 12px; }
    .state-executed { color: #9f9; }
    .state-self_aborted, .state-suppressed_by_leash { color: #fa0; }
    .state-denied, .state-failed { color: #f66; }
    .state-awaiting_confirmation { color: #99f; }
    .state-reverted { color: #aaa; }
    #ts { color: #888; font-size: 11px; margin-bottom: 10px; }
  </style>
  <script>
    async function refresh() {
      const resp = await fetch('/admin/audit?limit=200');
      const data = await resp.json();
      const tbody = document.getElementById('tbody');
      tbody.innerHTML = data.actions.map(a => `
        <tr>
          <td>${new Date(a.proposed_at).toLocaleTimeString()}</td>
          <td>${a.tool}</td>
          <td>${a.verb}</td>
          <td class="state-${a.state}">${a.state}</td>
          <td>${a.intent.substring(0, 80)}</td>
          <td>$${a.cost_usd.toFixed(4)}</td>
        </tr>
      `).join('');
      document.getElementById('ts').textContent = 'Updated: ' + new Date().toLocaleTimeString();
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</head>
<body>
  <h1>Audit Feed</h1>
  <div id="ts"></div>
  <table>
    <thead>
      <tr>
        <th>Time</th><th>Tool</th><th>Verb</th><th>State</th><th>Intent</th><th>Cost</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</body>
</html>
"""


@admin_router.get("/audit")
async def get_audit(limit: int = Query(200, le=500)) -> dict:
    actions = await audit.recent(n=limit)
    return {
        "count": len(actions),
        "actions": [
            {
                "id": a.id,
                "tool": a.tool,
                "verb": a.verb,
                "intent": a.intent,
                "preview": a.preview,
                "state": a.state,
                "authorization": a.authorization,
                "proposed_at": a.proposed_at.isoformat(),
                "cost_usd": a.cost_estimate.usd,
                "error": a.error,
            }
            for a in actions
        ],
    }


@admin_router.get("/audit/ui", response_class=HTMLResponse)
async def audit_ui():
    return AUDIT_HTML


@admin_router.get("/oauth/spotify/start")
async def spotify_oauth_start():
    s = get_settings()
    if not s.spotify_client_id:
        return HTMLResponse("<h1>Error</h1><p>SPOTIFY_CLIENT_ID not configured.</p>", status_code=500)

    from urllib.parse import urlencode
    params = {
        "client_id": s.spotify_client_id,
        "response_type": "code",
        "redirect_uri": s.spotify_redirect_uri,
        "scope": SPOTIFY_SCOPES,
        "state": "chloe-oauth-csrf",
    }
    return RedirectResponse(SPOTIFY_AUTH_URL + "?" + urlencode(params))


@admin_router.get("/oauth/spotify/callback")
async def spotify_oauth_callback(code: str = None, error: str = None, state: str = None):
    if error:
        return HTMLResponse(f"<h1>OAuth Error</h1><p>{error}</p>", status_code=400)
    if not code:
        return HTMLResponse("<h1>Error</h1><p>No code received.</p>", status_code=400)

    s = get_settings()
    secret_val = s.spotify_client_secret.get_secret_value() if s.spotify_client_secret else ""
    creds = base64.b64encode(f"{s.spotify_client_id}:{secret_val}".encode()).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.spotify_redirect_uri,
            },
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    if resp.status_code != 200:
        log.error("spotify_token_exchange_failed", status=resp.status_code)
        return HTMLResponse(f"<h1>Token Error</h1><p>{resp.text}</p>", status_code=500)

    token_data = resp.json()
    store_token("spotify", token_data)

    async with httpx.AsyncClient() as client:
        profile_resp = await client.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
    display_name = (
        profile_resp.json().get("display_name", "Unknown")
        if profile_resp.status_code == 200
        else "Unknown"
    )

    log.info("spotify_oauth_complete", display_name=display_name)
    return HTMLResponse(f"""
        <h1>Spotify Connected</h1>
        <p>Connected as <strong>{display_name}</strong></p>
        <p><a href="/admin/">Back to admin</a></p>
    """)


@admin_router.get("/oauth/google/start")
async def google_oauth_start():
    s = get_settings()
    if not s.google_client_id:
        return HTMLResponse("<h1>Error</h1><p>GOOGLE_CLIENT_ID not configured.</p>", status_code=500)

    from urllib.parse import urlencode, quote
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": "chloe-google-csrf",
    }
    return RedirectResponse(GOOGLE_AUTH_URL + "?" + urlencode(params, quote_via=quote))


@admin_router.get("/cache/status")
async def cache_status():
    from chloe.llm.gemini import get_cache_name
    name = get_cache_name()
    return {
        "cache_name": name,
        "active": name is not None,
        "refresh_interval_seconds": 50 * 60,
        "ttl_seconds": 3600,
    }


@admin_router.post("/cache/reset")
async def cache_reset():
    from chloe.llm.gemini import cache_static_prefix
    name = await cache_static_prefix()
    return {"cache_name": name, "reset": True}


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
                "client_secret": s.google_client_secret.get_secret_value() if s.google_client_secret else "",
            },
        )

    if resp.status_code != 200:
        return HTMLResponse(f"<h1>Token Error</h1><p>{resp.text}</p>", status_code=500)

    token_data = resp.json()
    store_token("google", token_data)

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

import base64
import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from chloe.actions import audit
from chloe.config import get_settings
from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import store as store_token

admin_router = APIRouter()

log = get_logger("admin.oauth")

_DASHBOARD_HTML = Path(__file__).resolve().parents[2] / "Chloe Dashboard.html"


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
    if _DASHBOARD_HTML.exists():
        return FileResponse(_DASHBOARD_HTML, media_type="text/html")
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
    secret_val = s.spotify_client_secret if s.spotify_client_secret else ""
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
                "client_secret": s.google_client_secret if s.google_client_secret else "",
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


# ── Memory management ─────────────────────────────────────────────────────────

class MemoryInject(BaseModel):
    text: str
    kind: str = "episodic"
    source: str = "admin_inject"
    salience: float = 0.8
    weight: float = 1.0


@admin_router.post("/memories/inject")
async def inject_memory(body: MemoryInject) -> dict:
    from chloe.memory import store as mem_store
    memory_id = mem_store.add(
        kind=body.kind,
        text=body.text,
        source=body.source,
        salience=body.salience,
        weight=body.weight,
    )
    log.info("memory_injected", id=memory_id, kind=body.kind)
    return {"id": memory_id, "kind": body.kind, "text": body.text}


@admin_router.get("/memories")
async def list_memories(limit: int = Query(20, le=200)) -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, kind, text, weight, salience, source, created_at FROM memories ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {
        "count": len(rows),
        "memories": [dict(r) for r in rows],
    }


@admin_router.get("/live/recent")
async def live_recent() -> dict:
    from chloe.observability import live_buffer
    return live_buffer.snapshot()


@admin_router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    from chloe.state.db import get_connection
    from chloe.memory.store import delete_from_chroma
    conn = get_connection()
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    delete_from_chroma(memory_id)
    log.info("memory_deleted", id=memory_id)
    return {"deleted": memory_id}


class MemoryUpdate(BaseModel):
    text: str | None = None
    salience: float | None = None
    weight: float | None = None
    kind: str | None = None


@admin_router.patch("/memories/{memory_id}")
async def update_memory(memory_id: int, body: MemoryUpdate) -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    fields, vals = [], []
    if body.text is not None:
        fields.append("text = ?"); vals.append(body.text)
    if body.salience is not None:
        fields.append("salience = ?"); vals.append(body.salience)
    if body.weight is not None:
        fields.append("weight = ?"); vals.append(body.weight)
    if body.kind is not None:
        fields.append("kind = ?"); vals.append(body.kind)
    if not fields:
        return {"updated": memory_id, "changed": 0}
    vals.append(memory_id)
    conn.execute(f"UPDATE memories SET {', '.join(fields)} WHERE id = ?", vals)
    conn.commit()
    return {"updated": memory_id}


# ── Prompts ───────────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"


@admin_router.get("/prompts")
async def list_prompts_admin() -> dict:
    files = {}
    for f in sorted(_PROMPTS_DIR.glob("*.md")):
        try:
            files[f.stem] = f.read_text()
        except Exception:
            files[f.stem] = ""
    return {"prompts": files}


class PromptUpdate(BaseModel):
    content: str


@admin_router.put("/prompts/{name}")
async def update_prompt(name: str, body: PromptUpdate) -> dict:
    if not name.replace("_", "").replace("-", "").isalnum():
        from fastapi import HTTPException
        raise HTTPException(400, "invalid prompt name")
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "prompt not found")
    path.write_text(body.content)
    log.info("prompt_updated", name=name)
    return {"updated": name}


# ── KV state ──────────────────────────────────────────────────────────────────

@admin_router.get("/kv")
async def list_kv_admin() -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM kv ORDER BY key").fetchall()
    return {"kv": {r["key"]: r["value"] for r in rows}}


class KVSet(BaseModel):
    value: str


@admin_router.put("/kv/{key:path}")
async def set_kv_admin(key: str, body: KVSet) -> dict:
    from chloe.state.kv import set as kv_set
    kv_set(key, body.value)
    return {"key": key, "set": True}


@admin_router.delete("/kv/{key:path}")
async def delete_kv_admin(key: str) -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    conn.commit()
    return {"key": key, "deleted": True}


# ── Persons ───────────────────────────────────────────────────────────────────

class PersonUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    impression: str | None = None
    trait_profile: dict | None = None
    attachment_pattern: str | None = None


@admin_router.put("/persons/{person_id}")
async def update_person(person_id: int, body: PersonUpdate) -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    fields, vals = [], []
    if body.name is not None and body.name.strip():
        fields.append("name = ?"); vals.append(body.name.strip())
    if body.aliases is not None:
        # Strip blanks and self-referential entries
        name_for_filter = (body.name or "").strip().lower()
        if not name_for_filter:
            row = conn.execute("SELECT name FROM persons WHERE id=?", (person_id,)).fetchone()
            name_for_filter = (row["name"] if row else "").lower()
        cleaned = [a.strip() for a in body.aliases if a.strip() and a.strip().lower() != name_for_filter]
        fields.append("aliases = ?"); vals.append(json.dumps(cleaned))
    if body.impression is not None:
        fields.append("impression = ?"); vals.append(body.impression)
    if body.trait_profile is not None:
        fields.append("trait_profile = ?"); vals.append(json.dumps(body.trait_profile))
    if body.attachment_pattern is not None:
        fields.append("attachment_pattern = ?"); vals.append(body.attachment_pattern)
    if not fields:
        return {"updated": person_id, "changed": 0}
    vals.append(person_id)
    conn.execute(f"UPDATE persons SET {', '.join(fields)} WHERE id = ?", vals)
    conn.commit()
    return {"updated": person_id}


# ── Reflect + synthesis controls ──────────────────────────────────────────────

@admin_router.post("/reflect/trigger")
async def trigger_reflect() -> dict:
    from chloe.reflect.every_2h import run as run_reflect
    import asyncio
    try:
        result = await asyncio.wait_for(run_reflect(force=True), timeout=60)
        return {"status": "ok", "result": str(result)}
    except asyncio.TimeoutError:
        return {"status": "timeout"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@admin_router.post("/teo-read/synthesize")
async def trigger_teo_read() -> dict:
    from chloe.reflect.weekly import run_teo_read_synthesis
    try:
        result = await run_teo_read_synthesis()
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ── Onboarding ────────────────────────────────────────────────────────────────

class OnboardingAnswer(BaseModel):
    question: str
    answer: str


class OnboardingComplete(BaseModel):
    answers: list[OnboardingAnswer]


@admin_router.get("/onboarding/status")
async def onboarding_status() -> dict:
    from chloe.state.kv import get as kv_get
    done = bool(kv_get("onboarding:teo:complete"))
    return {"complete": done}


@admin_router.post("/onboarding/complete")
async def complete_onboarding(body: OnboardingComplete) -> dict:
    from chloe.memory import store as mem_store
    from chloe.state.kv import set as kv_set
    import asyncio

    qa_text = "\n\n".join(
        f"Q: {item.question}\nA: {item.answer.strip()}"
        for item in body.answers
        if item.answer.strip()
    )

    # Store raw Q&A immediately so memories are available before extraction finishes
    raw_ids = []
    for item in body.answers:
        if item.answer.strip():
            mid = mem_store.add(
                kind="semantic",
                text=f"Teo told me: {item.answer.strip()}",
                source="onboarding",
                salience=0.85,
                weight=1.0,
                tags=["onboarding", "teo_profile"],
            )
            raw_ids.append(mid)

    kv_set("onboarding:teo:complete", "1")

    # Run extraction in background — don't block the response
    asyncio.create_task(_run_extraction_bg(qa_text))

    log.info("onboarding_complete", raw_memories=len(raw_ids))
    return {"status": "ok", "raw_memories": len(raw_ids), "extraction": "pending"}


async def _run_extraction_bg(qa_text: str) -> None:
    """Background task: structured extraction after onboarding answers are stored."""
    try:
        from chloe.identity.onboarding import run_extraction
        from chloe.state.db import get_connection
        conn = get_connection()
        result = await run_extraction(qa_text, conn)
        log.info("onboarding_extraction_bg_done",
                 knowledge=result.get("knowledge_statements", 0),
                 people=len(result.get("people_found") or []))
    except Exception as exc:
        log.warning("onboarding_extraction_bg_failed", error=str(exc))


@admin_router.post("/onboarding/re-extract")
async def re_extract_onboarding() -> dict:
    """Re-run structured extraction over existing onboarding memories."""
    from chloe.state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT text FROM memories WHERE source='onboarding' ORDER BY id ASC"
    ).fetchall()
    if not rows:
        return {"status": "skipped", "reason": "no_onboarding_memories"}

    # Reconstruct qa_text from stored memories
    qa_text = "\n\n".join(r["text"].replace("Teo told me: ", "A: ") for r in rows)
    extraction = await _run_onboarding_extraction(qa_text, conn)
    return {"status": "ok", **extraction}


async def _run_onboarding_extraction(qa_text: str, conn) -> dict:
    from chloe.identity.onboarding import run_extraction
    return await run_extraction(qa_text, conn)

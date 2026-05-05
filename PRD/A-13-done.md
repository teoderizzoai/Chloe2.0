# A-13 · Audit feed tab in admin dashboard

## Overview

Add a `/admin/audit` route returning the last 200 `actions` rows as JSON. Add a minimal HTML page (no framework — `<table>` is fine) at `/admin/audit/ui` that auto-refreshes every 5 seconds and displays the feed.

## Context

The audit feed is one of the primary trust surfaces. Teo needs to be able to open a browser and see exactly what Chloe has been doing — every outreach, every self-abort, every suppressed action. The HTML page is intentionally minimal for Phase A; it will be superseded by the mobile app's Activity tab in Phase F.

## API endpoint — `GET /admin/audit`

```python
# In chloe/admin/api.py

from fastapi import APIRouter, Query
from chloe.actions import audit
from chloe.actions.schema import Action
from datetime import datetime

admin_router = APIRouter()

@admin_router.get("/audit")
async def get_audit(limit: int = Query(200, le=500)) -> dict:
    """Return the last N actions as JSON."""
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
```

## HTML page — `GET /admin/audit/ui`

```python
@admin_router.get("/audit/ui", response_class=HTMLResponse)
async def audit_ui():
    return AUDIT_HTML

from fastapi.responses import HTMLResponse

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
```

## Dependencies

- A-03 (`audit.recent`).
- F-11 (`admin_router` already mounted in `app.py`).

## Testing

### Unit tests — `tests/unit/test_admin_audit.py`

```python
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from chloe.app import create_app
from chloe.state.db import migrate, close
from chloe.actions.schema import Action
from chloe.actions import audit
import asyncio

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

@pytest.fixture
def client():
    return TestClient(create_app())

def test_audit_endpoint_returns_200(client):
    response = client.get("/admin/audit")
    assert response.status_code == 200

def test_audit_endpoint_json_schema(client):
    response = client.get("/admin/audit")
    data = response.json()
    assert "count" in data
    assert "actions" in data
    assert isinstance(data["actions"], list)

def test_audit_endpoint_shows_actions(client):
    a = Action(
        tool="spotify", verb="queue_track",
        intent="play calming music",
        preview="Queue Bloom",
        authorization="kinetic",
        state="executed",
    )
    asyncio.get_event_loop().run_until_complete(audit.append(a))
    
    response = client.get("/admin/audit")
    data = response.json()
    assert data["count"] >= 1
    assert any(item["tool"] == "spotify" for item in data["actions"])

def test_audit_endpoint_correct_schema_fields(client):
    a = Action(
        tool="notes", verb="create",
        intent="write a note",
        preview="Create note",
        authorization="kinetic",
    )
    asyncio.get_event_loop().run_until_complete(audit.append(a))
    
    response = client.get("/admin/audit")
    item = response.json()["actions"][0]
    required_fields = {"id", "tool", "verb", "intent", "state", "authorization", "proposed_at"}
    assert required_fields.issubset(item.keys())

def test_audit_ui_returns_html(client):
    response = client.get("/admin/audit/ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Audit Feed" in response.text

def test_audit_limit_param(client):
    response = client.get("/admin/audit?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["actions"]) <= 5
```

## Acceptance criteria

- `GET /admin/audit` returns `200` with correct JSON schema.
- JSON body has `count` and `actions` fields; each action has `id`, `tool`, `verb`, `state`, `intent`, `proposed_at`.
- `GET /admin/audit/ui` returns an HTML page.
- Page auto-refreshes every 5 seconds (client-side JS verified by inspection).

# G-04 · HA entity allowlist + safety-device blocklist

## Overview

Two preference keys gate all HomeAssistant actions. `preferences["ha_allowlist"]` limits which entities Chloe can control — only listed entities are reachable. `preferences["ha_blocklist"]` explicitly bans safety-critical devices even if they appear on the allowlist. Gate checks the allowlist at submit time; blocklist check happens inside `smart_home.py` before any HTTP call.

## Context

The allowlist is the primary safety mechanism: Teo populates it with every entity Chloe should ever touch. The blocklist is a secondary hard stop for devices that are categorically off-limits regardless of future allowlist changes — door locks, the alarm system, smoke detectors. Both checks exist so that a misconfigured allowlist alone can't accidentally grant access to a dangerous device.

The gate-level check (allowlist) stops the action before it enters the tool at all, keeping the audit trail clean. The tool-level check (blocklist) is a defense-in-depth measure that fires even if the gate check is bypassed (e.g., direct tool invocation in debug mode).

## Implementation

### Gate-level allowlist check

```python
# In chloe/actions/gate.py — add to _pre_execute_checks():

async def _check_ha_allowlist(self, action: Action) -> str | None:
    """Return a denial reason if the HA entity is not on the allowlist."""
    if action.tool != "smart_home":
        return None

    entity = action.args.get("entity") or action.args.get("name")  # scene uses 'name'
    if not entity:
        return None  # No entity to check; let tool handle

    from chloe.state.db import get_connection
    import json
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM preferences WHERE key='ha_allowlist'"
    ).fetchone()
    if not row:
        return None  # No allowlist configured; allow all

    allowed = json.loads(row["value"])
    if entity not in allowed:
        return f"HA entity {entity!r} is not on the allowlist"
    return None
```

Wire into `gate.py`'s pre-execution check chain:

```python
# In gate.py — _pre_execute_checks() (called before tool.execute()):
denial = await self._check_ha_allowlist(action)
if denial:
    action.state = "held_back"
    action.denial_reason = denial
    return False
```

### Tool-level blocklist check (already in G-03 `smart_home.py`)

The `_check_blocklist()` method in `SmartHomeTool` is already implemented in G-03. This step adds the preference management endpoints and unit tests covering both layers.

### Preferences migration

The `preferences` table already exists (F-04). Seed default values for new installations:

```sql
-- In chloe/state/migrations/0005_ha_prefs.sql

INSERT OR IGNORE INTO preferences (key, value)
VALUES ('ha_allowlist', '[]');

INSERT OR IGNORE INTO preferences (key, value)
VALUES ('ha_blocklist', '["lock.front_door", "alarm_control_panel.home", "switch.smoke_detector"]');
```

### Admin endpoint to manage lists

```python
# chloe/channels/ha_prefs_routes.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from chloe.state.db import get_connection
import json

router = APIRouter(prefix="/admin/ha", tags=["ha-prefs"])


class EntityListUpdate(BaseModel):
    entities: list[str]


@router.get("/allowlist")
async def get_allowlist():
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='ha_allowlist'").fetchone()
    return {"entities": json.loads(row["value"]) if row else []}


@router.put("/allowlist")
async def set_allowlist(update: EntityListUpdate):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(update.entities)),
    )
    conn.commit()
    return {"status": "updated", "entities": update.entities}


@router.get("/blocklist")
async def get_blocklist():
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='ha_blocklist'").fetchone()
    return {"entities": json.loads(row["value"]) if row else []}


@router.put("/blocklist")
async def set_blocklist(update: EntityListUpdate):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(update.entities)),
    )
    conn.commit()
    return {"status": "updated", "entities": update.entities}
```

Mount in `app.py`:

```python
from chloe.channels.ha_prefs_routes import router as ha_prefs_router
app.include_router(ha_prefs_router)
```

## Testing

### Unit tests — `tests/unit/test_ha_allowlist_blocklist.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.state.db import migrate, close, get_connection
from chloe.tools.smart_home import SmartHomeTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()

@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr("chloe.tools.smart_home.get_settings", lambda: MagicMock(
        ha_base_url="http://ha.test:8123",
        ha_token="tok",
    ))
    return SmartHomeTool()


@pytest.mark.asyncio
async def test_allowlist_blocks_unlisted_entity(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.living_room"]))
    )
    db.commit()
    result = await tool.execute("lights", {"entity": "light.bedroom", "state": "on"})
    assert not result.success
    assert "allowlist" in result.error


@pytest.mark.asyncio
async def test_allowlist_permits_listed_entity(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.bedroom"]))
    )
    db.commit()
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [])
        )
        result = await tool.execute("lights", {"entity": "light.bedroom", "state": "on"})
    assert result.success


@pytest.mark.asyncio
async def test_blocklist_blocks_even_if_on_allowlist(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["lock.front_door"]))
    )
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(["lock.front_door"]))
    )
    db.commit()
    result = await tool.execute("lights", {"entity": "lock.front_door", "state": "off"})
    assert not result.success
    assert "blocklist" in result.error


@pytest.mark.asyncio
async def test_no_allowlist_permits_all(tool, db):
    """If ha_allowlist is not configured, all entities are permitted (relying on blocklist only)."""
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [])
        )
        result = await tool.execute("lights", {"entity": "light.any_entity", "state": "on"})
    assert result.success


@pytest.mark.asyncio
async def test_gate_denies_unlisted_entity():
    """Gate-level check: entity not in allowlist → action held_back."""
    from chloe.actions.gate import ActionGate
    from chloe.actions.models import Action
    import json
    from chloe.state.db import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.living_room"]))
    )
    conn.commit()

    gate = ActionGate()
    action = Action(tool="smart_home", verb="lights", args={"entity": "light.unlisted", "state": "on"})

    denial = await gate._check_ha_allowlist(action)
    assert denial is not None
    assert "allowlist" in denial


@pytest.mark.asyncio
async def test_gate_permits_listed_entity():
    from chloe.actions.gate import ActionGate
    from chloe.actions.models import Action
    import json
    from chloe.state.db import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.living_room"]))
    )
    conn.commit()

    gate = ActionGate()
    action = Action(tool="smart_home", verb="lights", args={"entity": "light.living_room", "state": "on"})

    denial = await gate._check_ha_allowlist(action)
    assert denial is None


def test_blocklist_entity_in_blocklist_errors_without_http(tool, db):
    """Blocklist check must fire before any HTTP call."""
    import asyncio
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(["switch.smoke_detector"]))
    )
    db.commit()

    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock()
        result = asyncio.get_event_loop().run_until_complete(
            tool.execute("lights", {"entity": "switch.smoke_detector", "state": "off"})
        )
        mock.return_value.__aenter__.return_value.post.assert_not_called()

    assert not result.success
```

### Manual UAT

1. Use `PUT /admin/ha/allowlist` to set `["light.living_room"]`.
2. Ask Chloe to turn on `light.living_room` — succeeds.
3. Ask Chloe to turn on `light.bedroom` — gate denies, audit shows `held_back`.
4. Add `lock.front_door` to both lists via API.
5. Ask Chloe to control `lock.front_door` — tool-level blocklist blocks it.

## Dependencies

- G-03 (`SmartHomeTool` — blocklist check already in `_check_blocklist()`).
- `0005_ha_prefs.sql` migration seeds default blocklist.
- A-04 (`gate.py` — `_pre_execute_checks()` chain).

## Acceptance criteria

- Entity not in allowlist → gate denies (`state="held_back"`) before tool called.
- Entity in blocklist → `PermissionError`-equivalent even if in allowlist.
- Empty allowlist (key missing) → all entities permitted.
- `PUT /admin/ha/allowlist` and `/admin/ha/blocklist` update preferences.
- No HTTP call to HA made when blocked.

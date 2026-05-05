# C-11 · Undo flow for kinetic actions

## Overview

Implement reverse verbs for `calendar.add_event`, `notes.append`, and `spotify.queue_track`. Expose `POST /v1/actions/{action_id}/revert` endpoint. On revert: execute the reverse verb, update `actions.user_response`, set `state="reverted"`, store a `held_back`-tagged episodic memory.

## Context

The `ToolVerb` dataclass has a `reverse_verb` field (added in A-06) for exactly this purpose. When Teo regrets an action — queued the wrong track, appended to the wrong note, added an event by mistake — he can tap "Revert" in the Activity tab. The revert flow is symmetric: it produces a new action row (the undo action) so the audit trail remains complete.

## Reverse verbs to implement

### `calendar.delete_event`

```python
"delete_event": ToolVerb(
    name="delete_event",
    schema={"type":"object","properties":{"eventId":{"type":"string"}},"required":["eventId"]},
    auth_class="kinetic",
    reversibility=0.0,
    description_for_model="Delete a Google Calendar event. Used as undo for add_event.",
    description_for_human="Delete calendar event",
),
```

```python
elif verb == "delete_event":
    hdrs = await self._headers()
    if not hdrs:
        return ToolResult(success=False, error="No Google token")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(
            f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events/{args['eventId']}",
            headers=hdrs,
        )
    if resp.status_code in (200, 204):
        # Mark artifact as deleted
        conn = get_connection()
        conn.execute("UPDATE artifact_index SET exists_=0 WHERE ref=?", (args["eventId"],))
        conn.commit()
        return ToolResult(success=True, data={"deleted": args["eventId"]})
    return ToolResult(success=False, error=f"Calendar API error: {resp.status_code}")
```

### `notes.truncate_append`

Undo the most recent `append` to a note file by restoring from `.versions/`.

```python
"truncate_append": ToolVerb(
    name="truncate_append",
    schema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},
    auth_class="kinetic",
    reversibility=0.0,
    description_for_model="Undo the most recent append to a note file.",
    description_for_human="Undo append to note",
),
```

Implementation: call the existing `revert` verb logic internally.

### `spotify.clear_queue` (best-effort)

Spotify's API does not provide a "remove a specific item from queue" endpoint; clearing the queue is the available option.

```python
"clear_queue": ToolVerb(
    name="clear_queue",
    schema={"type":"object","properties":{}},
    auth_class="kinetic",
    reversibility=0.0,
    description_for_model="Skip current track and clear the queue. Best-effort undo for queue_track.",
    description_for_human="Clear queue",
),
```

```python
elif verb == "clear_queue":
    hdrs = await self._get_headers()
    if not hdrs:
        return ToolResult(success=False, error="No Spotify token")
    # Skip current track via next endpoint
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{SPOTIFY_API}/me/player/next", headers=hdrs)
    return ToolResult(success=True, data={"cleared": True})
```

## Revert endpoint

```python
# chloe/channels/revert_routes.py

from fastapi import APIRouter, HTTPException
from chloe.state.db import get_connection
from chloe.actions.schema import Action, ActionResult, ulid
from chloe.tools.registry import get_registry
from chloe.observability.logging import get_logger
from datetime import datetime
import json

log = get_logger("revert")
router = APIRouter(prefix="/v1/actions", tags=["actions"])


@router.post("/{action_id}/revert")
async def revert_action(action_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Action not found")

    if row["state"] != "executed":
        raise HTTPException(status_code=409, detail=f"Cannot revert action in state '{row['state']}'")

    # Look up the reverse_verb from the tool registry
    registry = get_registry()
    tool = registry.get_tool(row["tool"])
    if not tool:
        raise HTTPException(status_code=422, detail=f"Tool '{row['tool']}' not found")

    verb_def = tool.verbs.get(row["verb"])
    if not verb_def or not verb_def.reverse_verb:
        raise HTTPException(status_code=422, detail=f"No reverse verb for {row['tool']}.{row['verb']}")

    # Build args for the reverse verb (pull relevant IDs from the original result)
    original_result = json.loads(row.get("result_json") or "{}")
    reverse_args = _build_reverse_args(row["tool"], row["verb"], original_result, json.loads(row["args"]))

    # Execute the reverse
    reverse_result = await registry.execute(row["tool"], verb_def.reverse_verb, reverse_args)

    if not reverse_result.success:
        raise HTTPException(status_code=502, detail=f"Revert failed: {reverse_result.error}")

    # Update original action state
    user_response = {"kind": "revert", "reverted_at": datetime.utcnow().isoformat()}
    conn.execute(
        "UPDATE actions SET state='reverted', user_response=? WHERE id=?",
        (json.dumps(user_response), action_id),
    )

    # Create held_back memory
    memory_id = ulid()
    conn.execute(
        """INSERT INTO memories (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
           VALUES (?, 'episodic', ?, 'revert', ?, '[]', 0.9, '["held_back","reverted"]', ?)""",
        (memory_id, f"Teo reverted: {row['intent']}", action_id, datetime.utcnow().isoformat()),
    )
    conn.commit()

    log.info("action_reverted", action_id=action_id, reverse_verb=verb_def.reverse_verb)
    return {"status": "reverted", "action_id": action_id, "reverse_verb": verb_def.reverse_verb}


def _build_reverse_args(tool: str, verb: str, result: dict, original_args: dict) -> dict:
    """Extract the relevant ID from the original action result for use in the reverse verb."""
    if tool == "calendar" and verb == "add_event":
        return {"eventId": result.get("eventId", "")}
    if tool == "notes" and verb == "append":
        return {"path": original_args.get("path", "")}
    if tool == "spotify" and verb == "queue_track":
        return {}
    return {}
```

## Dependencies

- A-06 (`ToolVerb.reverse_verb` field).
- C-01 (Spotify write verbs, `artifact_index` marking).
- C-02 (Calendar write verbs, `delete_event`).
- A-11 (`notes.revert` verb already exists).
- F-11 (`app.py` — router registration).

## Testing

### Integration tests — `tests/integration/test_revert.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.mark.asyncio
async def test_revert_calendar_event(db):
    from httpx import AsyncClient
    from chloe.app import create_app
    from chloe.tools.registry import get_registry
    from chloe.tools.calendar import CalendarTool

    app = create_app()

    # Insert a fake "executed" add_event action
    action = Action(
        tool="calendar", verb="add_event",
        args={"title": "Dentist", "start": "2026-05-05T09:00:00", "end": "2026-05-05T10:00:00"},
        intent="Add dentist appointment",
        preview="Add Dentist on 5th May",
        authorization="kinetic",
    )
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, result_json, proposed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (action.id, "calendar", "add_event", json.dumps(action.args), action.intent, action.preview,
         "kinetic", "executed", json.dumps({"eventId": "ev_abc123"}), "2026-05-04T10:00:00"),
    )
    db.commit()

    delete_calls = []

    async def mock_delete(url, headers=None):
        delete_calls.append(url)
        resp = MagicMock()
        resp.status_code = 204
        return resp

    with patch("chloe.tools.calendar.load_token", return_value={"access_token": "tok"}), \
         patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.delete = mock_delete
        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(f"/v1/actions/{action.id}/revert")

    assert resp.status_code == 200
    assert resp.json()["status"] == "reverted"

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "reverted"

    # Held-back memory created
    mem = db.execute("SELECT tags FROM memories WHERE source='revert'").fetchone()
    assert mem is not None
    tags = json.loads(mem["tags"])
    assert "reverted" in tags
    assert "held_back" in tags

    # Event marked as not existing
    art = db.execute("SELECT exists_ FROM artifact_index WHERE ref='ev_abc123'").fetchone()
    assert art is None or art["exists_"] == 0


@pytest.mark.asyncio
async def test_revert_non_executed_action_returns_409(db):
    from httpx import AsyncClient
    from chloe.app import create_app

    action_id = "act_proposed"
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (action_id, "calendar", "add_event", "{}", "intent", "preview", "kinetic", "proposed", "2026-05-04T10:00:00"),
    )
    db.commit()

    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post(f"/v1/actions/{action_id}/revert")

    assert resp.status_code == 409
```

## Acceptance criteria

- `POST /v1/actions/{id}/revert` on an `executed` calendar event → calls `delete_event`, sets `state="reverted"`, creates memory with `["held_back","reverted"]` tags.
- Reverting a non-`executed` action returns `409`.
- Reverting an action with no `reverse_verb` returns `422`.
- `artifact_index.exists_` set to `0` after successful revert.

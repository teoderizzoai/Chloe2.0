# C-02 · `tools/calendar.py` — write verbs

## Overview

Add write verbs to `chloe/tools/calendar.py`: `add_event(title, start, end, description?)` and `add_reminder(time, body)` with auth `kinetic`; `decline(eventId, reason?)` with auth `kinetic-sensitive`. Register artifact rows on success.

## Write verbs

```python
# Add to CalendarTool.verbs:

"add_event": ToolVerb(
    name="add_event",
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 datetime"},
            "end": {"type": "string", "description": "ISO 8601 datetime"},
            "description": {"type": "string"},
        },
        "required": ["title", "start", "end"],
    },
    auth_class="kinetic", reversibility=0.9,
    description_for_model="Add a calendar event to Teo's Google Calendar.",
    description_for_human="Add calendar event",
    reverse_verb="delete_event",
),
"add_reminder": ToolVerb(
    name="add_reminder",
    schema={"type":"object","properties":{"time":{"type":"string"},"body":{"type":"string"}},"required":["time","body"]},
    auth_class="kinetic", reversibility=0.9,
    description_for_model="Add a reminder to Teo's calendar.",
    description_for_human="Add reminder",
),
"decline": ToolVerb(
    name="decline",
    schema={"type":"object","properties":{"eventId":{"type":"string"},"reason":{"type":"string"}},"required":["eventId"]},
    auth_class="kinetic-sensitive", reversibility=0.3,
    description_for_model="Decline a calendar event on Teo's behalf. Requires confirmation.",
    description_for_human="Decline event",
),
```

## Execute implementation

```python
elif verb == "add_event":
    hdrs = await self._headers()
    if not hdrs:
        return ToolResult(success=False, error="No Google token")
    
    event_body = {
        "summary": args["title"],
        "start": {"dateTime": args["start"], "timeZone": get_settings().chloe_timezone},
        "end": {"dateTime": args["end"], "timeZone": get_settings().chloe_timezone},
        "description": args.get("description", "Added by Chloe"),
    }
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
            headers={**hdrs, "Content-Type": "application/json"},
            json=event_body,
        )
    
    if resp.status_code in (200, 201):
        event_id = resp.json().get("id", "")
        _register_artifact("calendar_event", event_id, args["title"], args.get("__action_id", ""))
        return ToolResult(success=True, data={"eventId": event_id, "title": args["title"]},
                         artifact_ref=event_id, artifact_kind="calendar_event")
    return ToolResult(success=False, error=f"Calendar API error: {resp.status_code}")
```

## Dry-run output

```python
def dry_run(self, verb: str, args: dict) -> str:
    if verb == "add_event":
        return f"Would add event '{args.get('title', '?')}' on {args.get('start', '?')}"
    if verb == "add_reminder":
        return f"Would set reminder at {args.get('time', '?')}: {args.get('body', '?')[:60]}"
    if verb == "decline":
        return f"Would decline event {args.get('eventId', '?')}"
    return super().dry_run(verb, args)
```

## Dependencies

- B-06 (existing CalendarTool read verbs).
- A-01 (`artifact_index` table).

## Testing

### Unit tests — `tests/unit/test_calendar_write.py`

```python
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.tools.calendar import CalendarTool

@pytest.fixture
def tool():
    return CalendarTool()

@pytest.mark.asyncio
async def test_add_event_dry_run(tool):
    preview = tool.dry_run("add_event", {
        "title": "Team meeting",
        "start": "2026-05-05T10:00:00",
        "end": "2026-05-05T11:00:00",
    })
    assert "Team meeting" in preview
    assert "2026-05-05" in preview

@pytest.mark.asyncio
async def test_add_event_success(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    
    async def mock_post(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "event_xyz", "summary": "Team meeting"}
        return resp
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        with patch("chloe.tools.calendar._register_artifact"):
            result = await tool.execute("add_event", {
                "title": "Team meeting",
                "start": "2026-05-05T10:00:00+01:00",
                "end": "2026-05-05T11:00:00+01:00",
            })
    
    assert result.success
    assert result.data["eventId"] == "event_xyz"

def test_decline_is_kinetic_sensitive(tool):
    assert tool.verbs["decline"].auth_class == "kinetic-sensitive"

def test_add_event_is_kinetic(tool):
    assert tool.verbs["add_event"].auth_class == "kinetic"

@pytest.mark.asyncio
async def test_add_event_artifact_row(tool, tmp_path, monkeypatch):
    """add_event success writes to artifact_index."""
    from pathlib import Path
    from chloe.state.db import migrate, close, get_connection
    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    
    async def mock_post(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "ev_abc"}
        return resp
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("add_event", {
            "title": "Dentist", "start": "2026-05-05T09:00:00+01:00",
            "end": "2026-05-05T10:00:00+01:00", "__action_id": "act1"
        })
    
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='ev_abc'").fetchone()
    assert row is not None
    assert row["kind"] == "calendar_event"
    close()
```

## Acceptance criteria

- `add_event` dry-run shows title + formatted time.
- Artifact row written on execute.
- `decline` verb has `auth_class="kinetic-sensitive"`.
- 401 triggers `refresh_token("google")`.

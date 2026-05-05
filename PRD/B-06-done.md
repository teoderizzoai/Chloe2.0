# B-06 · `tools/calendar.py` — read verbs

## Overview

Implement `chloe/tools/calendar.py` with read verbs: `read_today()`, `read_week()`, `find_free_slot(duration_minutes, between)`. Auth: `intimate`. Returns structured dicts. Uses the Google Calendar API with the stored Google OAuth token.

## Context

Calendar reads are one of the most important context signals for the initiative engine. Knowing that Teo has a dentist appointment at 09:00 lets Chloe send the right message at the right time. `read_today()` is used by the opportunity vector (D-07), and `find_free_slot` lets Chloe suggest times intelligently.

## Implementation

```python
# chloe/tools/calendar.py

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.state.oauth_tokens import load as load_token, refresh as refresh_token
from chloe.observability.logging import get_logger
from datetime import datetime, date, timedelta, timezone
import httpx

log = get_logger("tool.calendar")
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
CALENDAR_ID = "primary"

class CalendarTool(Tool):
    name = "calendar"

    def __init__(self):
        self.verbs = {
            "read_today": ToolVerb(
                name="read_today",
                schema={"type": "object", "properties": {}},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Read Teo's Google Calendar events for today.",
                description_for_human="Read today's calendar",
            ),
            "read_week": ToolVerb(
                name="read_week",
                schema={"type": "object", "properties": {}},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Read Teo's Google Calendar events for this week.",
                description_for_human="Read week's calendar",
            ),
            "find_free_slot": ToolVerb(
                name="find_free_slot",
                schema={
                    "type": "object",
                    "properties": {
                        "duration_minutes": {"type": "integer"},
                        "between": {
                            "type": "object",
                            "properties": {
                                "start": {"type": "string", "description": "ISO datetime"},
                                "end": {"type": "string", "description": "ISO datetime"},
                            },
                        },
                    },
                    "required": ["duration_minutes"],
                },
                auth_class="intimate", reversibility=1.0,
                description_for_model="Find a free time slot in Teo's calendar.",
                description_for_human="Find free slot",
            ),
        }

    async def _headers(self) -> dict | None:
        token = load_token("google")
        if not token:
            return None
        return {"Authorization": f"Bearer {token['access_token']}"}

    async def _get_events(self, time_min: str, time_max: str) -> list | None:
        hdrs = await self._headers()
        if not hdrs:
            return None
        params = {
            "calendarId": CALENDAR_ID,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                headers=hdrs,
                params=params,
            )
            if resp.status_code == 401:
                new_token = await refresh_token("google")
                if new_token:
                    hdrs = {"Authorization": f"Bearer {new_token['access_token']}"}
                    resp = await client.get(
                        f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                        headers=hdrs, params=params,
                    )
            if resp.status_code != 200:
                log.error("calendar_api_error", status=resp.status_code)
                return None
            return resp.json().get("items", [])

    def _format_event(self, item: dict) -> dict:
        start = item.get("start", {})
        end = item.get("end", {})
        return {
            "title": item.get("summary", ""),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "location": item.get("location"),
            "description": item.get("description", "")[:200],
            "id": item.get("id", ""),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        from chloe.config import get_settings
        tz_str = get_settings().chloe_timezone
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = timezone.utc

        now = datetime.now(tz)

        if verb == "read_today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            events = await self._get_events(
                start.isoformat(), end.isoformat()
            )
            if events is None:
                return ToolResult(success=False, error="Failed to read calendar")
            return ToolResult(success=True, data={"events": [self._format_event(e) for e in events]})

        elif verb == "read_week":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            events = await self._get_events(start.isoformat(), end.isoformat())
            if events is None:
                return ToolResult(success=False, error="Failed to read calendar")
            return ToolResult(success=True, data={"events": [self._format_event(e) for e in events]})

        elif verb == "find_free_slot":
            duration = args.get("duration_minutes", 30)
            between = args.get("between", {})
            search_start = datetime.fromisoformat(between.get("start", now.isoformat()))
            search_end = datetime.fromisoformat(between.get("end", (now + timedelta(days=1)).isoformat()))
            
            events = await self._get_events(search_start.isoformat(), search_end.isoformat())
            if events is None:
                return ToolResult(success=False, error="Failed to read calendar")
            
            # Simple slot-finding: scan for gaps
            busy = []
            for e in events:
                start_str = e.get("start", {}).get("dateTime")
                end_str = e.get("end", {}).get("dateTime")
                if start_str and end_str:
                    busy.append((datetime.fromisoformat(start_str), datetime.fromisoformat(end_str)))
            busy.sort()
            
            cursor = search_start
            for b_start, b_end in busy:
                if (b_start - cursor).total_seconds() >= duration * 60:
                    return ToolResult(success=True, data={
                        "slot_start": cursor.isoformat(),
                        "slot_end": (cursor + timedelta(minutes=duration)).isoformat(),
                    })
                cursor = max(cursor, b_end)
            
            if (search_end - cursor).total_seconds() >= duration * 60:
                return ToolResult(success=True, data={
                    "slot_start": cursor.isoformat(),
                    "slot_end": (cursor + timedelta(minutes=duration)).isoformat(),
                })
            
            return ToolResult(success=True, data={"slot_start": None, "message": "No free slot found"})

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
```

## Dependencies

- A-06 (Tool base).
- B-01 (OAuth tokens).

## Testing

### VCR cassette tests — `tests/unit/test_calendar_read.py`

```python
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from chloe.tools.calendar import CalendarTool

MOCK_EVENTS = [
    {
        "id": "event1",
        "summary": "Dentist",
        "start": {"dateTime": "2026-05-04T09:00:00+01:00"},
        "end": {"dateTime": "2026-05-04T10:00:00+01:00"},
        "location": "Via Roma 1",
    },
    {
        "id": "event2",
        "summary": "Team standup",
        "start": {"dateTime": "2026-05-04T10:30:00+01:00"},
        "end": {"dateTime": "2026-05-04T11:00:00+01:00"},
    },
]

@pytest.fixture
def tool():
    return CalendarTool()

@pytest.mark.asyncio
async def test_read_today_returns_events(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    
    with patch.object(tool, "_get_events", return_value=MOCK_EVENTS):
        result = await tool.execute("read_today", {})
    
    assert result.success
    assert len(result.data["events"]) == 2
    assert result.data["events"][0]["title"] == "Dentist"
    assert result.data["events"][0]["location"] == "Via Roma 1"

@pytest.mark.asyncio
async def test_read_today_event_schema(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get_events", return_value=MOCK_EVENTS):
        result = await tool.execute("read_today", {})
    event = result.data["events"][0]
    assert {"title", "start", "end"}.issubset(event.keys())

@pytest.mark.asyncio
async def test_find_free_slot_basic(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get_events", return_value=[]):
        result = await tool.execute("find_free_slot", {
            "duration_minutes": 30,
            "between": {
                "start": "2026-05-04T08:00:00+00:00",
                "end": "2026-05-04T18:00:00+00:00",
            }
        })
    assert result.success
    assert result.data.get("slot_start") is not None

def test_all_verbs_intimate_auth(tool):
    for verb, vobj in tool.verbs.items():
        assert vobj.auth_class == "intimate"
```

## Acceptance criteria

- VCR cassette tests pass.
- `read_today` returns `list[{title, start, end, location?}]`.
- `find_free_slot` finds a gap between existing events.
- 401 triggers `refresh_token("google")`.

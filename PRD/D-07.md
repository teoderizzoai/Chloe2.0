# D-07 · `initiative/opportunity.py` — world-opportunity Flash call

## Overview

Implement `get_opportunity_vector() -> OpportunityVector`. Makes a Flash call every 10 minutes using `opportunity_vector.md` prompt. Result cached in `kv["opp_vector_cache"]` with a timestamp. Second call within 10 minutes returns cached value without a new LLM call.

## Context

The opportunity vector captures contextual readiness signals — time of day, recent chat activity, calendar events, whether Spotify is playing, user location inferred from calendar. It weights each channel's receptivity as a float 0–1. The scoring formula in D-08 multiplies each candidate's tool-weight by this vector, so a candidate for `messages.send_text` is weighted by `opp.messages`. This makes Chloe context-aware: she won't initiate a conversation when the calendar shows Teo is in a 3-hour meeting.

## Implementation

```python
# chloe/initiative/opportunity.py

import json
from datetime import datetime, timedelta
from chloe.llm.gemini import get_client as get_llm
from chloe.llm.schemas import OpportunityVector
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger
from chloe.tools.calendar import CalendarTool

log = get_logger("initiative.opportunity")

CACHE_KEY = "opp_vector_cache"
CACHE_TTL_MINUTES = 10


async def get_opportunity_vector() -> OpportunityVector:
    """
    Return the current world-opportunity vector.
    Result is cached for 10 minutes in kv to avoid repeated Flash calls.
    """
    cached = kv_get(CACHE_KEY)
    if cached:
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        if datetime.utcnow() - cached_at < timedelta(minutes=CACHE_TTL_MINUTES):
            log.debug("opp_vector_cache_hit")
            return OpportunityVector(**cached["vector"])

    vector = await _compute_vector()
    kv_set(CACHE_KEY, {
        "vector": vector.model_dump(),
        "cached_at": datetime.utcnow().isoformat(),
    })
    return vector


async def _compute_vector() -> OpportunityVector:
    """Build the opportunity vector via a Flash call."""
    now = datetime.now()
    context = await _gather_context(now)

    llm = get_llm()
    result = await llm.flash("opportunity_vector.md", context, schema=OpportunityVector)

    if result is None:
        log.warning("opp_vector_llm_failed_using_defaults")
        return _default_vector(now)

    try:
        return OpportunityVector(**result) if isinstance(result, dict) else result
    except Exception as exc:
        log.warning("opp_vector_parse_error", error=str(exc))
        return _default_vector(now)


async def _gather_context(now: datetime) -> dict:
    """Gather world context for the opportunity vector prompt."""
    calendar_events = []
    try:
        from chloe.tools.calendar import CalendarTool
        cal = CalendarTool()
        result = await cal.execute("read_today", {})
        if result.success:
            calendar_events = result.data.get("events", [])
    except Exception:
        pass

    return {
        "time_of_day": now.strftime("%H:%M"),
        "day_of_week": now.strftime("%A"),
        "hour": now.hour,
        "is_weekend": now.weekday() >= 5,
        "calendar_events_today": calendar_events,
        "last_chat_seen": kv_get("last_chat_seen", default="unknown"),
        "spotify_playing": kv_get("spotify_is_playing", default=False),
    }


def _default_vector(now: datetime) -> OpportunityVector:
    """Return a sensible default when LLM is unavailable."""
    hour = now.hour
    # Lower message opportunity during typical sleep/work hours
    msg_opp = 0.8 if 9 <= hour <= 22 else 0.2
    return OpportunityVector(
        messages=msg_opp,
        spotify=0.5,
        calendar=0.4,
        notes=0.6,
        web_search=0.7,
        gmail=0.3,
        reminders=0.4,
    )
```

## `opportunity_vector.md` prompt template

```markdown
# World Opportunity Vector

Assess the current opportunity for Chloe to take actions across different channels.

## Context
- Time: {{time_of_day}} {{day_of_week}}
- Calendar today: {{calendar_events_today}}
- Last chat with Teo: {{last_chat_seen}}
- Spotify playing: {{spotify_playing}}

## Output
Return JSON matching the OpportunityVector schema:
{
  "messages": 0.0-1.0,    # How receptive is Teo to receiving a message right now?
  "spotify": 0.0-1.0,     # Is music context-appropriate?
  "calendar": 0.0-1.0,    # Would a calendar action be timely?
  "notes": 0.0-1.0,       # Is now a good time to add to notes?
  "web_search": 0.0-1.0,  # Can Chloe usefully search right now?
  "gmail": 0.0-1.0,       # Is email action appropriate?
  "reminders": 0.0-1.0    # Would a reminder be useful?
}

Low message opportunity: Teo is in back-to-back meetings, it's 02:00, or last chat was < 5 min ago.
High message opportunity: Morning/evening, last chat > 4 hours ago, no events blocking.
```

## `OpportunityVector` schema (in `llm/schemas.py`)

```python
class OpportunityVector(BaseModel):
    messages: float = Field(ge=0.0, le=1.0)
    spotify: float = Field(ge=0.0, le=1.0)
    calendar: float = Field(ge=0.0, le=1.0)
    notes: float = Field(ge=0.0, le=1.0)
    web_search: float = Field(ge=0.0, le=1.0)
    gmail: float = Field(ge=0.0, le=1.0)
    reminders: float = Field(ge=0.0, le=1.0)
```

## Dependencies

- F-05 (`llm/gemini.py` — Flash call).
- F-06 (`llm/schemas.py` — `OpportunityVector` schema).
- F-08 (`state/kv.py` — caching).
- B-06 (`tools/calendar.py` — read today's events).

## Testing

### Unit tests — `tests/unit/test_opportunity.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from chloe.initiative.opportunity import get_opportunity_vector, CACHE_KEY, CACHE_TTL_MINUTES
from chloe.llm.schemas import OpportunityVector


@pytest.fixture(autouse=True)
def clear_kv_cache(monkeypatch):
    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", lambda key, **kw: None)
    monkeypatch.setattr("chloe.initiative.opportunity.kv_set", lambda key, val: None)


@pytest.mark.asyncio
async def test_first_call_makes_flash_call(monkeypatch):
    mock_flash = AsyncMock(return_value={
        "messages": 0.8, "spotify": 0.6, "calendar": 0.4,
        "notes": 0.7, "web_search": 0.8, "gmail": 0.3, "reminders": 0.4,
    })
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=mock_flash))
    monkeypatch.setattr("chloe.initiative.opportunity._gather_context", AsyncMock(return_value={}))

    vec = await get_opportunity_vector()
    assert mock_flash.call_count == 1
    assert vec.messages == 0.8


@pytest.mark.asyncio
async def test_cached_call_skips_flash(monkeypatch):
    now = datetime.utcnow()
    cached_vector = {
        "messages": 0.9, "spotify": 0.5, "calendar": 0.3,
        "notes": 0.6, "web_search": 0.7, "gmail": 0.2, "reminders": 0.3,
    }

    def mock_kv_get(key, **kw):
        if key == CACHE_KEY:
            return {
                "vector": cached_vector,
                "cached_at": now.isoformat(),
            }
        return None

    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", mock_kv_get)

    mock_flash = AsyncMock()
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=mock_flash))

    vec = await get_opportunity_vector()
    assert mock_flash.call_count == 0  # Cache hit — no LLM call
    assert vec.messages == 0.9


@pytest.mark.asyncio
async def test_stale_cache_refreshes(monkeypatch):
    stale_time = (datetime.utcnow() - timedelta(minutes=CACHE_TTL_MINUTES + 1)).isoformat()

    def mock_kv_get(key, **kw):
        if key == CACHE_KEY:
            return {
                "vector": {"messages": 0.1, "spotify": 0.1, "calendar": 0.1,
                           "notes": 0.1, "web_search": 0.1, "gmail": 0.1, "reminders": 0.1},
                "cached_at": stale_time,
            }
        return None

    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", mock_kv_get)
    monkeypatch.setattr("chloe.initiative.opportunity.kv_set", lambda k, v: None)

    mock_flash = AsyncMock(return_value={
        "messages": 0.8, "spotify": 0.6, "calendar": 0.4,
        "notes": 0.7, "web_search": 0.8, "gmail": 0.3, "reminders": 0.4,
    })
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=mock_flash))
    monkeypatch.setattr("chloe.initiative.opportunity._gather_context", AsyncMock(return_value={}))

    vec = await get_opportunity_vector()
    assert mock_flash.call_count == 1  # Cache expired — new call made
    assert vec.messages == 0.8


@pytest.mark.asyncio
async def test_llm_failure_returns_default(monkeypatch):
    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", lambda k, **kw: None)
    monkeypatch.setattr("chloe.initiative.opportunity.kv_set", lambda k, v: None)
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=AsyncMock(return_value=None)))
    monkeypatch.setattr("chloe.initiative.opportunity._gather_context", AsyncMock(return_value={}))

    vec = await get_opportunity_vector()
    assert isinstance(vec, OpportunityVector)
    assert 0.0 <= vec.messages <= 1.0  # Some sensible default
```

## Acceptance criteria

- First call makes exactly one Flash call and caches the result.
- Second call within 10 minutes uses cache — zero Flash calls.
- Cache older than 10 minutes → refreshes with a new Flash call.
- LLM failure → returns a sensible default `OpportunityVector` without raising.

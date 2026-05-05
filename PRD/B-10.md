# B-10 · Phase B acceptance test

## Overview

Write the Phase B acceptance test: a scripted replay that (1) Spotify read → audit entry; (2) Gmail read → audit entry; (3) Calendar read → audit entry; (4) chat turn → reply text contains words from the audit context. Assert: all reads have `authorization="intimate"`, no kinetic actions fired, no PII query reached Brave.

## Context

This is the gate test for Phase B promotion. It verifies that the three read tools are wired correctly through the gate, that the audit feed captures intimate-class reads, and that the chat context assembly incorporates recent audit activity. All three vendor tools run with mocked responses (no live API calls in CI).

## Test file — `tests/integration/test_phase_b_acceptance.py`

```python
import pytest
import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions import gate, audit
from chloe.tools.spotify import SpotifyTool
from chloe.tools.gmail import GmailTool
from chloe.tools.calendar import CalendarTool
from chloe.tools.registry import ToolRegistry

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(SpotifyTool())
    r.register(GmailTool())
    r.register(CalendarTool())
    return r


# ──────────────────────────────────────────────────────────────
# Mock data
# ──────────────────────────────────────────────────────────────

SPOTIFY_NOW_PLAYING = {
    "track": "Funeral", "artist": "Phoebe Bridgers",
    "progress_ms": 45000, "is_playing": True, "uri": "spotify:track:abc"
}
GMAIL_MESSAGES = {
    "messages": [{"id": "m1", "subject": "Meeting tomorrow", "from": "alice@example.com",
                  "date": "2026-05-04", "snippet": "Don't forget our meeting tomorrow"}]
}
CALENDAR_EVENTS = {
    "events": [{"title": "Dentist", "start": "2026-05-04T09:00:00+01:00",
                "end": "2026-05-04T10:00:00+01:00", "location": "Via Roma"}]
}


@pytest.mark.asyncio
async def test_phase_b_all_reads_in_audit(registry, monkeypatch):
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    
    # Mock all tool executions
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    
    with patch.object(SpotifyTool, "_call", AsyncMock(return_value={
        "item": {"name": "Funeral", "artists": [{"name": "PB"}], "uri": "spotify:track:abc"},
        "progress_ms": 0, "is_playing": True
    })), \
    patch.object(GmailTool, "_get", side_effect=[
        {"messages": [{"id": "m1", "threadId": "t1"}]},
        {"id": "m1", "threadId": "t1", "snippet": "Meeting tomorrow", "payload": {
            "headers": [{"name": "From", "value": "alice@x.com"},
                        {"name": "Subject", "value": "Meeting tomorrow"},
                        {"name": "Date", "value": "Mon"}]
        }}
    ]), \
    patch.object(CalendarTool, "_get_events", AsyncMock(return_value=[
        {"id": "ev1", "summary": "Dentist",
         "start": {"dateTime": "2026-05-04T09:00:00+01:00"},
         "end": {"dateTime": "2026-05-04T10:00:00+01:00"}}
    ])):
        
        # (1) Spotify read
        a1 = Action(tool="spotify", verb="show_currently_playing", args={},
                    intent="check what's playing", preview="Check Spotify", authorization="intimate")
        r1 = await gate.submit(a1)
        
        # (2) Gmail read
        a2 = Action(tool="gmail", verb="read_recent", args={"limit": 1},
                    intent="check inbox", preview="Check Gmail", authorization="intimate")
        r2 = await gate.submit(a2)
        
        # (3) Calendar read
        a3 = Action(tool="calendar", verb="read_today", args={},
                    intent="check schedule", preview="Check Calendar", authorization="intimate")
        r3 = await gate.submit(a3)
    
    # All succeeded
    assert r1.executed
    assert r2.executed
    assert r3.executed
    
    conn = get_connection()
    
    # All reads have authorization="intimate" in DB
    rows = conn.execute(
        "SELECT authorization, state, tool FROM actions ORDER BY proposed_at"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["authorization"] == "intimate"
        assert row["state"] == "executed"
    
    # Spotify, Gmail, Calendar all present
    tools = {r["tool"] for r in rows}
    assert tools == {"spotify", "gmail", "calendar"}
    
    # No kinetic actions fired
    kinetic_count = conn.execute(
        "SELECT COUNT(*) FROM actions WHERE authorization IN ('kinetic','kinetic-sensitive')"
    ).fetchone()[0]
    assert kinetic_count == 0


@pytest.mark.asyncio
async def test_audit_context_in_chat_suffix_after_reads():
    """After reads, build_dynamic_suffix includes their info."""
    a = Action(tool="spotify", verb="show_currently_playing", args={},
               intent="check Spotify to know the vibe",
               preview="Check Spotify", authorization="intimate", state="executed")
    await audit.append(a)
    
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix("teo")
    assert "spotify" in suffix
    assert "show_currently_playing" in suffix


@pytest.mark.asyncio
async def test_no_pii_query_reaches_brave():
    """PII-filtered queries never reach the Brave API."""
    conn = get_connection()
    conn.execute("INSERT INTO persons (name, aliases, work_domains) VALUES ('Bob Jones', '[]', '[]')")
    conn.commit()
    
    brave_called = []
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=lambda *a, **kw: brave_called.append(kw) or MagicMock(
                status_code=200, json=lambda: {"web": {"results": []}}
            )
        ):
            a = Action(tool="web_search", verb="search",
                       args={"query": "bob jones email address"},
                       intent="research", preview="search", authorization="free")
            result = await gate.submit(a)
    
    assert result.suppressed
    assert len(brave_called) == 0  # Brave was never called
```

## Acceptance criteria

- All three reads produce `actions` rows with `authorization="intimate"`.
- No kinetic action rows.
- `build_dynamic_suffix` after reads contains at least one tool name from the reads.
- PII-blocked query: zero calls to Brave API, memory row with `"refusal"` tag.

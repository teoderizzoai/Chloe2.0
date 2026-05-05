import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from chloe.actions import audit, gate
from chloe.actions.schema import Action
from chloe.state.db import close, get_connection, migrate
from chloe.tools.calendar import CalendarTool
from chloe.tools.gmail import GmailTool
from chloe.tools.registry import ToolRegistry
from chloe.tools.spotify import SpotifyTool

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


@pytest.mark.asyncio
async def test_phase_b_all_reads_in_audit(registry, monkeypatch):
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})

    spotify_data = {
        "item": {"name": "Funeral", "artists": [{"name": "PB"}], "uri": "spotify:track:abc"},
        "progress_ms": 0,
        "is_playing": True,
    }
    gmail_list = {"messages": [{"id": "m1", "threadId": "t1"}]}
    gmail_detail = {
        "id": "m1", "threadId": "t1", "snippet": "Meeting tomorrow",
        "payload": {"headers": [
            {"name": "From", "value": "alice@x.com"},
            {"name": "Subject", "value": "Meeting tomorrow"},
            {"name": "Date", "value": "Mon"},
        ]},
    }
    calendar_events = [{
        "id": "ev1", "summary": "Dentist",
        "start": {"dateTime": "2026-05-04T09:00:00+01:00"},
        "end": {"dateTime": "2026-05-04T10:00:00+01:00"},
    }]

    with patch.object(SpotifyTool, "_call", AsyncMock(return_value=spotify_data)), \
         patch.object(GmailTool, "_get", side_effect=[gmail_list, gmail_detail]), \
         patch.object(CalendarTool, "_get_events", AsyncMock(return_value=calendar_events)):

        a1 = Action(tool="spotify", verb="show_currently_playing", args={},
                    intent="check what's playing", preview="Check Spotify", authorization="intimate")
        r1 = await gate.submit(a1)

        a2 = Action(tool="gmail", verb="read_recent", args={"limit": 1},
                    intent="check inbox", preview="Check Gmail", authorization="intimate")
        r2 = await gate.submit(a2)

        a3 = Action(tool="calendar", verb="read_today", args={},
                    intent="check schedule", preview="Check Calendar", authorization="intimate")
        r3 = await gate.submit(a3)

    assert r1.executed
    assert r2.executed
    assert r3.executed

    conn = get_connection()
    rows = conn.execute(
        "SELECT authorization, state, tool FROM actions ORDER BY proposed_at"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["authorization"] == "intimate"
        assert row["state"] == "executed"

    tools = {r["tool"] for r in rows}
    assert tools == {"spotify", "gmail", "calendar"}

    kinetic_count = conn.execute(
        "SELECT COUNT(*) FROM actions WHERE authorization IN ('kinetic','kinetic-sensitive')"
    ).fetchone()[0]
    assert kinetic_count == 0


@pytest.mark.asyncio
async def test_audit_context_in_chat_suffix_after_reads():
    a = Action(
        tool="spotify", verb="show_currently_playing", args={},
        intent="check Spotify to know the vibe",
        preview="Check Spotify", authorization="intimate", state="executed",
    )
    await audit.append(a)

    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix("teo")
    assert "spotify" in suffix
    assert "show_currently_playing" in suffix


@pytest.mark.asyncio
async def test_no_pii_query_reaches_brave():
    conn = get_connection()
    conn.execute("INSERT INTO persons (name, aliases, work_domains) VALUES ('Bob Jones', '[]', '[]')")
    conn.commit()

    brave_called = []

    async def mock_get(url, **kwargs):
        brave_called.append(url)
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"web": {"results": []}}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        a = Action(
            tool="web_search", verb="search",
            args={"query": "bob jones email address"},
            intent="research", preview="search", authorization="free",
        )
        result = await gate.submit(a)

    assert result.suppressed
    assert len(brave_called) == 0

    mem_row = conn.execute(
        "SELECT tags FROM memories WHERE source_ref=? AND source='action'", (a.id,)
    ).fetchone()
    assert mem_row is not None
    tags = json.loads(mem_row["tags"])
    assert "refusal" in tags

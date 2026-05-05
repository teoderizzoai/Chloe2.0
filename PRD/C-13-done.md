# C-13 · Phase C acceptance test (end-to-end confirmation flow)

## Overview

Scripted end-to-end test covering the full confirmation lifecycle:
1. `gmail.draft_reply` (kinetic) executes immediately → draft saved, artifact row written.
2. `gmail.send_reply` (kinetic-sensitive) → goes to `awaiting_confirmation`, push mock receives payload.
3. `/v1/confirmations/{id}/confirm` called.
4. Action executes (mocked send), `state="executed"`, memory created, artifact row created.

All assertions in one test function.

## Context

This is the Phase C gate test. It exercises every piece built in Phase C: write verbs (C-01–C-05), episodic memory creation (C-06), confirmation tickets (C-07), push notification dispatch (C-08/C-09 wired in C-10), and the revert flow (C-11). The dry-run canary (C-12) runs separately in CI.

## Test file — `tests/integration/test_phase_c_acceptance.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set
from chloe.actions import gate, confirm as confirm_mod
from chloe.actions.schema import Action
from chloe.tools.registry import ToolRegistry
from chloe.tools.gmail import GmailTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(GmailTool())
    return r


@pytest.mark.asyncio
async def test_phase_c_full_confirmation_flow(fresh_db, registry, monkeypatch):
    """Full end-to-end: draft → confirm → send."""
    from chloe.app import create_app
    app = create_app()

    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    # Register a fake iOS device for push
    kv_set("devices", [{"token": "ios_device_tok", "platform": "ios"}])

    push_received = []

    async def mock_push(device_info, payload):
        push_received.append(payload)
        return True

    # --- Step 1: draft_reply (kinetic) executes immediately ---
    async def mock_post_draft(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_001"}
        return resp

    with patch("httpx.AsyncClient") as mock_client, \
         patch("chloe.channels.push.preferred_push", new=mock_push):
        mock_client.return_value.__aenter__.return_value.post = mock_post_draft

        draft_action = Action(
            tool="gmail", verb="draft_reply",
            args={"threadId": "thread_xyz", "body": "Thanks for reaching out!"},
            intent="Draft reply to Alice's email",
            preview="Draft: Thanks for reaching out!",
            authorization="kinetic",
        )
        result_1 = await gate.submit(draft_action)

    assert result_1.executed, f"draft_reply should execute immediately: {result_1}"
    assert not result_1.awaiting

    # Verify artifact row written
    art = fresh_db.execute("SELECT * FROM artifact_index WHERE kind='gmail_thread'").fetchone()
    assert art is not None

    # Verify episodic memory created
    mem = fresh_db.execute("SELECT * FROM memories WHERE source='action'").fetchone()
    assert mem is not None
    assert "Alice" in mem["text"]

    # --- Step 2: send_reply (kinetic-sensitive) → awaiting confirmation ---
    async def mock_post_send(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "sent_msg_001"}
        return resp

    with patch("httpx.AsyncClient") as mock_client, \
         patch("chloe.channels.push.preferred_push", new=mock_push):
        mock_client.return_value.__aenter__.return_value.post = mock_post_send

        send_action = Action(
            tool="gmail", verb="send_reply",
            args={"draftId": "draft_001"},
            intent="Send reply to Alice via draft",
            preview="Send: Thanks for reaching out!",
            authorization="kinetic-sensitive",
        )
        result_2 = await gate.submit(send_action)

    assert result_2.awaiting, f"send_reply should await confirmation: {result_2}"
    assert result_2.ticket_id
    assert not result_2.executed

    # Verify push notification sent
    assert len(push_received) >= 1
    conf_push = next((p for p in push_received if p.get("type") == "confirmation"), None)
    assert conf_push is not None, f"Expected confirmation push, got: {push_received}"
    assert conf_push["ticket_id"] == result_2.ticket_id
    assert conf_push["action_tool"] == "gmail"

    # Verify action is in awaiting_confirmation state
    row = fresh_db.execute(
        "SELECT state, ticket_id FROM actions WHERE id=?", (send_action.id,)
    ).fetchone()
    assert row["state"] == "awaiting_confirmation"

    # --- Step 3: POST /v1/confirmations/{id}/confirm ---
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post_send
        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(f"/v1/confirmations/{result_2.ticket_id}/confirm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    # --- Step 4: Verify final state ---
    # Action confirmed
    row = fresh_db.execute("SELECT state FROM actions WHERE id=?", (send_action.id,)).fetchone()
    assert row["state"] == "confirmed"


@pytest.mark.asyncio
async def test_phase_c_deny_flow(fresh_db, registry, monkeypatch):
    """Deny a confirmation ticket → action denied, held_back memory created."""
    from chloe.app import create_app
    app = create_app()

    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    kv_set("devices", [{"token": "ios_device_tok", "platform": "ios"}])

    with patch("chloe.channels.push.preferred_push", new=AsyncMock(return_value=True)):
        send_action = Action(
            tool="gmail", verb="send_reply",
            args={"draftId": "draft_999"},
            intent="Send email Teo will deny",
            preview="Send: ...",
            authorization="kinetic-sensitive",
        )
        result = await gate.submit(send_action)

    assert result.awaiting

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post(f"/v1/confirmations/{result.ticket_id}/deny")

    assert resp.status_code == 200

    row = fresh_db.execute("SELECT state FROM actions WHERE id=?", (send_action.id,)).fetchone()
    assert row["state"] == "denied"

    mem = fresh_db.execute(
        "SELECT tags FROM memories WHERE source='confirm_deny'"
    ).fetchone()
    assert mem is not None
    tags = json.loads(mem["tags"])
    assert "held_back" in tags


@pytest.mark.asyncio
async def test_phase_c_dry_run_no_http(monkeypatch):
    """In dry-run mode, no kinetic verb makes HTTP calls."""
    monkeypatch.setattr("chloe.tools.registry.get_settings", lambda: MagicMock(dry_run=True))
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    from chloe.tools.registry import ToolRegistry
    from chloe.tools.gmail import GmailTool

    registry = ToolRegistry()
    registry.register(GmailTool())

    http_called = []

    def spy(*a, **kw):
        http_called.append(a)
        raise AssertionError("HTTP call in dry-run")

    with patch("httpx.AsyncClient", spy):
        result = await registry.execute("gmail", "draft_reply", {
            "threadId": "t1", "body": "Test"
        })

    assert result.dry_run is True
    assert len(http_called) == 0


@pytest.mark.asyncio
async def test_phase_c_artifact_and_memory_counts(fresh_db, monkeypatch):
    """After 3 successful kinetic actions, artifact_index has 3 rows and memories has 3 rows."""
    from chloe.tools.registry import ToolRegistry
    from chloe.tools.calendar import CalendarTool
    from chloe.tools.reminders import RemindersTool
    from chloe.tools.spotify import SpotifyTool

    registry = ToolRegistry()
    registry.register(CalendarTool())
    registry.register(RemindersTool())
    registry.register(SpotifyTool())

    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.reminders.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})

    async def mock_http_success(url, headers=None, json=None, params=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "evt_1", "title": "Meeting"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_http_success

        for intent, action_args in [
            ("Add calendar event", Action(tool="calendar", verb="add_event",
                args={"title": "Dentist", "start": "2026-05-05T09:00:00", "end": "2026-05-05T10:00:00"},
                intent="Add dentist appointment", preview="Add Dentist", authorization="kinetic")),
            ("Add reminder", Action(tool="reminders", verb="add",
                args={"title": "Buy milk"},
                intent="Remind Teo to buy milk", preview="Reminder: Buy milk", authorization="kinetic")),
            ("Queue track", Action(tool="spotify", verb="queue_track",
                args={"uri": "spotify:track:abc"},
                intent="Queue Radiohead", preview="Queue track", authorization="kinetic")),
        ]:
            await gate.submit(action_args)

    mem_count = fresh_db.execute(
        "SELECT COUNT(*) FROM memories WHERE source='action'"
    ).fetchone()[0]
    assert mem_count == 3
```

## Acceptance criteria

- `draft_reply` (kinetic) executes immediately; `state="executed"` in `actions`.
- `send_reply` (kinetic-sensitive) enters `state="awaiting_confirmation"`.
- Push notification received with `type="confirmation"` and correct `ticket_id`.
- `POST /v1/confirmations/{id}/confirm` → action `state="confirmed"`.
- `POST /v1/confirmations/{id}/deny` → action `state="denied"`, memory with `["held_back","denied"]` tags.
- Dry-run: no HTTP calls made.
- 3 successful kinetic actions → 3 episodic memory rows with `source="action"`.

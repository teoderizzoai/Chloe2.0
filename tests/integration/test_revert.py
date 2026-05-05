import httpx
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.tools.registry import ToolRegistry
from chloe.tools.calendar import CalendarTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(CalendarTool())
    return r


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.mark.asyncio
async def test_revert_calendar_event(db, registry):
    from httpx import AsyncClient
    from chloe.app import create_app

    app = create_app()

    action = Action(
        tool="calendar", verb="add_event",
        args={"title": "Dentist", "start": "2026-05-05T09:00:00", "end": "2026-05-05T10:00:00"},
        intent="Add dentist appointment",
        preview="Add Dentist on 5th May",
        authorization="kinetic",
    )
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, result, proposed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
         patch("httpx.AsyncClient") as mock_client, \
         patch("chloe.channels.revert_routes.get_registry", return_value=registry):
        mock_client.return_value.__aenter__.return_value.delete = mock_delete
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/v1/actions/{action.id}/revert")

    assert resp.status_code == 200
    assert resp.json()["status"] == "reverted"

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "reverted"

    mem = db.execute("SELECT tags FROM memories WHERE source='revert'").fetchone()
    assert mem is not None
    tags = json.loads(mem["tags"])
    assert "reverted" in tags
    assert "held_back" in tags


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
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/v1/actions/{action_id}/revert")

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_revert_no_reverse_verb_returns_422(db):
    from httpx import AsyncClient
    from chloe.app import create_app

    action_id = "act_no_rev"
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, result, proposed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (action_id, "calendar", "read_today", "{}", "intent", "preview", "intimate", "executed", "{}", "2026-05-04T10:00:00"),
    )
    db.commit()

    app = create_app()
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/v1/actions/{action_id}/revert")

    assert resp.status_code in (404, 422)

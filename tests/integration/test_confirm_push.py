import httpx
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set
from chloe.actions.schema import Action
from chloe.actions import confirm as confirm_mod

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_action(db, action):
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (action.id, action.tool, action.verb, "{}", action.intent, action.preview,
         action.authorization, "proposed", "2026-05-04T10:00:00"),
    )
    db.commit()


@pytest.mark.asyncio
async def test_send_ticket_triggers_push(db):
    kv_set("devices", [{"token": "device_tok_123", "platform": "ios"}])

    push_received = []

    async def mock_preferred_push(device_info, payload):
        push_received.append({"device_info": device_info, "payload": payload})
        return True

    with patch("chloe.channels.push.preferred_push", new=mock_preferred_push):
        action = Action(
            tool="gmail", verb="send_reply",
            args={"draftId": "d1"},
            intent="Send the reply email",
            preview="Send reply to Alice",
            authorization="kinetic-sensitive",
        )
        _insert_action(db, action)
        ticket = await confirm_mod.send(action)

    assert len(push_received) == 1
    payload = push_received[0]["payload"]
    assert payload["type"] == "confirmation"
    assert payload["ticket_id"] == ticket.id
    assert payload["action_tool"] == "gmail"
    assert "Alice" in payload["preview"]


@pytest.mark.asyncio
async def test_send_ticket_no_device_does_not_raise(db):
    kv_set("devices", [])

    action = Action(
        tool="gmail", verb="send_reply",
        args={"draftId": "d1"},
        intent="Send email",
        preview="Send email",
        authorization="kinetic-sensitive",
    )
    _insert_action(db, action)

    ticket = await confirm_mod.send(action)
    assert ticket is not None


@pytest.mark.asyncio
async def test_confirm_endpoint(db):
    from httpx import AsyncClient
    from chloe.app import create_app
    app = create_app()

    kv_set("devices", [{"token": "dev_tok", "platform": "ios"}])

    action = Action(
        tool="gmail", verb="send_reply",
        args={"draftId": "d1"},
        intent="Send email",
        preview="Send email",
        authorization="kinetic-sensitive",
    )
    _insert_action(db, action)

    with patch("chloe.channels.push.preferred_push", new=AsyncMock(return_value=True)):
        ticket = await confirm_mod.send(action)

    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/v1/confirmations/{ticket.id}/confirm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_register_device_endpoint(db):
    from httpx import AsyncClient
    from chloe.app import create_app
    from chloe.state.kv import get as kv_get

    app = create_app()
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/devices", json={"token": "tok_ios_1", "platform": "ios"})

    assert resp.status_code == 200
    devices = kv_get("devices", default=[])
    assert any(d["token"] == "tok_ios_1" for d in devices)

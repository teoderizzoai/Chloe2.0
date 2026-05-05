import json
import pytest
from datetime import datetime
from pathlib import Path

from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions.confirm import (
    ConfirmationTicket,
    TICKET_TTL_MINUTES,
    confirm,
    deny,
    expire_pending,
    get_pending,
    send,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _make_action(**kwargs):
    defaults = dict(
        tool="gmail", verb="send_reply",
        args={"draftId": "d1"},
        intent="Send reply to email",
        preview="Send reply",
        authorization="kinetic-sensitive",
    )
    defaults.update(kwargs)
    return Action(**defaults)


def _insert_action(db, action):
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (action.id, action.tool, action.verb, "{}", action.intent, action.preview,
         action.authorization, "proposed", datetime.utcnow().isoformat()),
    )
    db.commit()


@pytest.mark.asyncio
async def test_send_creates_ticket(db):
    action = _make_action()
    _insert_action(db, action)

    import unittest.mock as m
    with m.patch("chloe.channels.push.preferred_push", new=m.AsyncMock(return_value=True)), \
         m.patch("chloe.channels.push.get_teo_device_info", return_value={}):
        ticket = await send(action)

    assert ticket.state == "pending"
    assert ticket.action_id == action.id

    row = db.execute("SELECT state, ticket_id FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "awaiting_confirmation"
    assert row["ticket_id"] == ticket.id


@pytest.mark.asyncio
async def test_confirm_ticket(db):
    action = _make_action()
    _insert_action(db, action)

    import unittest.mock as m
    with m.patch("chloe.channels.push.get_teo_device_info", return_value={}):
        ticket = await send(action)

    result = await confirm(ticket.id)
    assert result is True

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "confirmed"


@pytest.mark.asyncio
async def test_deny_creates_held_back_memory(db):
    action = _make_action()
    _insert_action(db, action)

    import unittest.mock as m
    with m.patch("chloe.channels.push.get_teo_device_info", return_value={}):
        ticket = await send(action)

    result = await deny(ticket.id)
    assert result is True

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "denied"

    mem_row = db.execute("SELECT tags FROM memories WHERE source='confirm_deny'").fetchone()
    assert mem_row is not None
    tags = json.loads(mem_row["tags"])
    assert "held_back" in tags


@pytest.mark.asyncio
async def test_expire_pending_marks_stale_as_denied(db, monkeypatch):
    action = _make_action()
    _insert_action(db, action)

    import unittest.mock as m
    with m.patch("chloe.channels.push.get_teo_device_info", return_value={}):
        ticket = await send(action)

    from chloe.actions import confirm as confirm_mod
    original_class = confirm_mod.ConfirmationTicket

    class FakeTicket(original_class):
        @property
        def is_stale(self):
            return True

    monkeypatch.setattr(confirm_mod, "ConfirmationTicket", FakeTicket)

    count = await expire_pending()
    assert count >= 1

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "denied"


@pytest.mark.asyncio
async def test_get_pending_returns_active_tickets(db):
    action = _make_action()
    _insert_action(db, action)

    import unittest.mock as m
    with m.patch("chloe.channels.push.get_teo_device_info", return_value={}):
        ticket = await send(action)

    pending = get_pending()
    assert any(t.id == ticket.id for t in pending)

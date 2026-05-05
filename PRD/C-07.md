# C-07 · `actions/confirm.py` — confirmation ticket lifecycle

## Overview

Implement `chloe/actions/confirm.py` with the `ConfirmationTicket` model and functions: `send(action)`, `confirm(ticket_id)`, `deny(ticket_id)`, `expire_pending()`. Tickets are persisted in `kv`. On `send()`, update `actions.state = "awaiting_confirmation"`. On `confirm()`, re-evaluate staleness and re-run deliberation if TTL exceeded. On `deny()`, store a `held_back` memory. `expire_pending()` runs every 5 minutes via the background task loop.

## Context

The confirmation flow is the safety layer for kinetic-sensitive actions. Without it, Chloe could send emails or toggle smart home devices without Teo knowing. The ticket system is the bridge between the gate (which produces a ticket for kinetic-sensitive actions) and the mobile app (which renders the confirmation UI). Tickets have a TTL (default 60 minutes); stale tickets are auto-denied to prevent Chloe from executing stale intents.

## Data model

```python
# chloe/actions/confirm.py

from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from typing import Literal
from chloe.actions.schema import ulid

TICKET_TTL_MINUTES = 60

class ConfirmationTicket(BaseModel):
    id: str = Field(default_factory=ulid)
    action_id: str
    preview: str
    action_tool: str
    action_verb: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(default=None)
    state: Literal["pending", "confirmed", "denied", "expired"] = "pending"

    def model_post_init(self, __context):
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(minutes=TICKET_TTL_MINUTES)

    @property
    def is_stale(self) -> bool:
        return datetime.utcnow() > self.expires_at
```

## Implementation

```python
import json
import asyncio
from chloe.state.kv import get as kv_get, set as kv_set, get_all as kv_get_all, delete as kv_delete
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("confirm")

KV_PREFIX = "ticket:"


def _ticket_key(ticket_id: str) -> str:
    return f"{KV_PREFIX}{ticket_id}"


async def send(action) -> ConfirmationTicket:
    """Create and persist a confirmation ticket; update action state."""
    ticket = ConfirmationTicket(
        action_id=action.id,
        preview=action.preview,
        action_tool=action.tool,
        action_verb=action.verb,
    )
    kv_set(_ticket_key(ticket.id), ticket.model_dump(mode="json"))

    conn = get_connection()
    conn.execute(
        "UPDATE actions SET state='awaiting_confirmation', ticket_id=? WHERE id=?",
        (ticket.id, action.id),
    )
    conn.commit()
    log.info("ticket_sent", ticket_id=ticket.id, action_id=action.id)
    return ticket


async def confirm(ticket_id: str) -> bool:
    """Confirm a ticket. Re-runs deliberation if ticket is stale."""
    data = kv_get(_ticket_key(ticket_id))
    if not data:
        log.warning("ticket_not_found", ticket_id=ticket_id)
        return False

    ticket = ConfirmationTicket(**data)
    if ticket.state != "pending":
        log.warning("ticket_not_pending", ticket_id=ticket_id, state=ticket.state)
        return False

    if ticket.is_stale:
        # Re-deliberate
        from chloe.actions.deliberate import deliberate
        from chloe.state.db import get_connection as gc
        action_row = gc().execute(
            "SELECT * FROM actions WHERE id=?", (ticket.action_id,)
        ).fetchone()
        if action_row:
            from chloe.actions.schema import Action
            action = Action(**dict(action_row))
            verdict = await deliberate(action, context={})
            if verdict and verdict.decision != "proceed":
                await deny(ticket_id)
                return False

    ticket.state = "confirmed"
    kv_set(_ticket_key(ticket_id), ticket.model_dump(mode="json"))

    conn = get_connection()
    conn.execute(
        "UPDATE actions SET state='confirmed' WHERE id=?",
        (ticket.action_id,),
    )
    conn.commit()
    log.info("ticket_confirmed", ticket_id=ticket_id)
    return True


async def deny(ticket_id: str) -> bool:
    """Deny a ticket; create held_back memory."""
    data = kv_get(_ticket_key(ticket_id))
    if not data:
        return False

    ticket = ConfirmationTicket(**data)
    ticket.state = "denied"
    kv_set(_ticket_key(ticket_id), ticket.model_dump(mode="json"))

    conn = get_connection()
    conn.execute(
        "UPDATE actions SET state='denied', user_response=? WHERE id=?",
        (json.dumps({"kind": "deny", "ticket_id": ticket_id}), ticket.action_id),
    )

    # Store held_back memory
    from chloe.actions.schema import ulid as _ulid
    from datetime import datetime
    memory_id = _ulid()
    action_row = conn.execute("SELECT * FROM actions WHERE id=?", (ticket.action_id,)).fetchone()
    intent_text = action_row["intent"] if action_row else f"Action {ticket.action_id}"
    conn.execute(
        """
        INSERT INTO memories (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
        VALUES (?, 'episodic', ?, 'confirm_deny', ?, '[]', 0.8, '["held_back","denied"]', ?)
        """,
        (memory_id, f"Teo denied: {intent_text}", ticket.action_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    log.info("ticket_denied", ticket_id=ticket_id)
    return True


async def expire_pending() -> int:
    """Expire all pending tickets past their TTL. Returns count expired."""
    all_kv = kv_get_all()
    expired_count = 0
    for key, value in all_kv.items():
        if not key.startswith(KV_PREFIX):
            continue
        try:
            ticket = ConfirmationTicket(**value)
        except Exception:
            continue
        if ticket.state == "pending" and ticket.is_stale:
            await deny(ticket.id)
            expired_count += 1
            log.info("ticket_expired", ticket_id=ticket.id)
    return expired_count


def get_pending() -> list[ConfirmationTicket]:
    """Return all pending (non-expired, non-denied) tickets."""
    all_kv = kv_get_all()
    tickets = []
    for key, value in all_kv.items():
        if not key.startswith(KV_PREFIX):
            continue
        try:
            ticket = ConfirmationTicket(**value)
            if ticket.state == "pending" and not ticket.is_stale:
                tickets.append(ticket)
        except Exception:
            pass
    return tickets
```

## Wire into gate.py

```python
# In gate.py: handle kinetic-sensitive actions

elif action.authorization == "kinetic-sensitive":
    from chloe.actions.confirm import send as send_ticket
    ticket = await send_ticket(action)
    return ActionResult(
        executed=False,
        suppressed=False,
        awaiting=True,
        ticket_id=ticket.id,
        reason=f"Awaiting confirmation (ticket {ticket.id})",
    )
```

## Dependencies

- A-01 (`actions` table with `ticket_id` and `state` columns).
- A-08 (`gate.py`).
- F-08 (`state/kv.py`).
- D-01 (`deliberate.py` — imported lazily for staleness re-check).

## Testing

### Unit tests — `tests/unit/test_confirm.py`

```python
import pytest
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions.confirm import (
    ConfirmationTicket, send, confirm, deny, expire_pending, get_pending, TICKET_TTL_MINUTES
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


@pytest.mark.asyncio
async def test_send_creates_ticket(db):
    action = _make_action()
    db.execute("INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "proposed", datetime.utcnow().isoformat()))
    db.commit()

    ticket = await send(action)
    assert ticket.state == "pending"
    assert ticket.action_id == action.id

    row = db.execute("SELECT state, ticket_id FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "awaiting_confirmation"
    assert row["ticket_id"] == ticket.id


@pytest.mark.asyncio
async def test_confirm_ticket(db):
    action = _make_action()
    db.execute("INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "awaiting_confirmation", datetime.utcnow().isoformat()))
    db.commit()

    ticket = await send(action)
    result = await confirm(ticket.id)
    assert result is True

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "confirmed"


@pytest.mark.asyncio
async def test_deny_creates_held_back_memory(db):
    action = _make_action()
    db.execute("INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "awaiting_confirmation", datetime.utcnow().isoformat()))
    db.commit()

    ticket = await send(action)
    result = await deny(ticket.id)
    assert result is True

    row = db.execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "denied"

    mem_row = db.execute("SELECT tags FROM memories WHERE source='confirm_deny'").fetchone()
    assert mem_row is not None
    import json
    tags = json.loads(mem_row["tags"])
    assert "held_back" in tags


@pytest.mark.asyncio
async def test_expire_pending_marks_stale_as_denied(db, monkeypatch):
    action = _make_action()
    db.execute("INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "awaiting_confirmation", datetime.utcnow().isoformat()))
    db.commit()

    ticket = await send(action)

    # Artificially expire by patching is_stale
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
    db.execute("INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "proposed", datetime.utcnow().isoformat()))
    db.commit()

    ticket = await send(action)
    pending = get_pending()
    assert any(t.id == ticket.id for t in pending)
```

## Acceptance criteria

- `send(action)` → action state is `"awaiting_confirmation"`, ticket in `kv`.
- `confirm(ticket_id)` → action state is `"confirmed"`.
- `deny(ticket_id)` → action state is `"denied"`, memory row with `["held_back","denied"]` tags.
- `expire_pending()` → stale pending tickets become `"denied"`.
- Stale ticket confirmed after TTL → re-deliberation triggered before proceeding.

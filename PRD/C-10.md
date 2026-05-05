# C-10 · Wire confirmation push to `confirm.py`

## Overview

In `confirm.send()`, after persisting the ticket, call `channels.preferred_push(teo_device_info, confirmation_payload)`. Add HTTP endpoints `POST /v1/confirmations/{ticket_id}/confirm` and `POST /v1/confirmations/{ticket_id}/deny` (the deep-link targets from the push notification buttons). Add `GET /v1/confirmations/pending` to list pending tickets.

## Context

C-07 built the ticket lifecycle; C-08 and C-09 built the push clients. This step wires them together so that when `confirm.send()` is called, Teo's phone receives a push notification with Yes/No buttons. The HTTP endpoints are the targets for those buttons — they're called by the mobile app when Teo taps a button or by the deep-link in the push notification action.

## Changes to `confirm.py`

```python
# In confirm.send(), after kv_set(...):

async def send(action) -> ConfirmationTicket:
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

    # Push notification to Teo's device
    from chloe.channels.push import preferred_push, get_teo_device_info
    device_info = get_teo_device_info()
    if device_info:
        push_payload = {
            "type": "confirmation",
            "preview": action.preview,
            "ticket_id": ticket.id,
            "action_tool": action.tool,
            "action_verb": action.verb,
        }
        await preferred_push(device_info, push_payload)
    else:
        log.warning("confirm_no_device", ticket_id=ticket.id)

    return ticket
```

## HTTP endpoints

```python
# chloe/channels/confirm_routes.py

from fastapi import APIRouter, HTTPException
from chloe.actions.confirm import confirm, deny, get_pending, ConfirmationTicket
from chloe.observability.logging import get_logger

log = get_logger("confirm_routes")
router = APIRouter(prefix="/v1/confirmations", tags=["confirmations"])


@router.post("/{ticket_id}/confirm")
async def confirm_ticket(ticket_id: str):
    success = await confirm(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail="Ticket not found or already resolved")
    return {"status": "confirmed", "ticket_id": ticket_id}


@router.post("/{ticket_id}/deny")
async def deny_ticket(ticket_id: str):
    success = await deny(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail="Ticket not found or already resolved")
    return {"status": "denied", "ticket_id": ticket_id}


@router.get("/pending")
async def list_pending() -> list[dict]:
    tickets = get_pending()
    return [t.model_dump(mode="json") for t in tickets]
```

Register in `app.py`:

```python
from chloe.channels.confirm_routes import router as confirm_router
app.include_router(confirm_router)
```

## Device registration endpoint

```python
# Add to app.py or a separate devices_routes.py

from fastapi import APIRouter
from pydantic import BaseModel
from chloe.state.kv import get as kv_get, set as kv_set

devices_router = APIRouter(prefix="/v1/devices", tags=["devices"])

class DeviceRegistration(BaseModel):
    token: str
    platform: str  # "ios" or "android"

@devices_router.post("")
async def register_device(reg: DeviceRegistration):
    devices = kv_get("devices", default=[])
    # Replace existing token for same platform
    devices = [d for d in devices if d.get("platform") != reg.platform]
    devices.append({"token": reg.token, "platform": reg.platform})
    kv_set("devices", devices)
    return {"status": "registered"}

@devices_router.delete("/{service}")
async def revoke_oauth(service: str):
    from chloe.state.oauth_tokens import store
    store(service, {})  # Clear by storing empty
    return {"status": "revoked"}
```

## Dependencies

- C-07 (`confirm.py` — ticket lifecycle).
- C-08 (`push_apns.py`) + C-09 (`push_fcm.py`, `push.preferred_push`).
- F-11 (`app.py` — router registration).

## Testing

### Integration tests — `tests/integration/test_confirm_push.py`

```python
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
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


@pytest.mark.asyncio
async def test_send_ticket_triggers_push(db):
    # Register a fake device
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
        db.execute(
            "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "proposed", "2026-05-04T10:00:00"),
        )
        db.commit()

        ticket = await confirm_mod.send(action)

    assert len(push_received) == 1
    payload = push_received[0]["payload"]
    assert payload["type"] == "confirmation"
    assert payload["ticket_id"] == ticket.id
    assert payload["action_tool"] == "gmail"
    assert "Alice" in payload["preview"]


@pytest.mark.asyncio
async def test_send_ticket_no_device_does_not_raise(db):
    kv_set("devices", [])  # No device registered

    action = Action(
        tool="gmail", verb="send_reply",
        args={"draftId": "d1"},
        intent="Send email",
        preview="Send email",
        authorization="kinetic-sensitive",
    )
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "proposed", "2026-05-04T10:00:00"),
    )
    db.commit()

    # Should not raise even without a device
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
    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (action.id, action.tool, action.verb, "{}", action.intent, action.preview, action.authorization, "proposed", "2026-05-04T10:00:00"),
    )
    db.commit()

    with patch("chloe.channels.push.preferred_push", new=AsyncMock(return_value=True)):
        ticket = await confirm_mod.send(action)

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post(f"/v1/confirmations/{ticket.id}/confirm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
```

### Unit test for device registration

```python
@pytest.mark.asyncio
async def test_register_device_endpoint(db):
    from httpx import AsyncClient
    from chloe.app import create_app
    from chloe.state.kv import get as kv_get

    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/v1/devices", json={"token": "tok_ios_1", "platform": "ios"})

    assert resp.status_code == 200
    devices = kv_get("devices", default=[])
    assert any(d["token"] == "tok_ios_1" for d in devices)
```

## Acceptance criteria

- `confirm.send(action)` dispatches push with `type="confirmation"` and correct `ticket_id`.
- `POST /v1/confirmations/{id}/confirm` returns `200` and updates action state to `"confirmed"`.
- `POST /v1/confirmations/{id}/deny` returns `200` and creates `held_back` memory.
- `GET /v1/confirmations/pending` returns list of pending tickets.
- No device registered → `send()` logs warning but does not raise.

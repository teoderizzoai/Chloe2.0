import json
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from chloe.actions.schema import ulid
from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.state.kv import delete as kv_delete
from chloe.state.kv import get as kv_get
from chloe.state.kv import get_all as kv_get_all
from chloe.state.kv import set as kv_set

log = get_logger("confirm")

TICKET_TTL_MINUTES = 60
KV_PREFIX = "ticket:"


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
            object.__setattr__(self, "expires_at", self.created_at + timedelta(minutes=TICKET_TTL_MINUTES))

    @property
    def is_stale(self) -> bool:
        return datetime.utcnow() > self.expires_at


def _ticket_key(ticket_id: str) -> str:
    return f"{KV_PREFIX}{ticket_id}"


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
    log.info("ticket_sent", ticket_id=ticket.id, action_id=action.id)

    # C-10: push notification to Teo's device
    try:
        from chloe.channels.push import get_teo_device_info, preferred_push
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
    except Exception as exc:
        log.warning("confirm_push_failed", error=str(exc))

    return ticket


async def confirm(ticket_id: str) -> bool:
    data = kv_get(_ticket_key(ticket_id))
    if not data:
        log.warning("ticket_not_found", ticket_id=ticket_id)
        return False

    ticket = ConfirmationTicket(**data)
    if ticket.state != "pending":
        log.warning("ticket_not_pending", ticket_id=ticket_id, state=ticket.state)
        return False

    if ticket.is_stale:
        try:
            from chloe.actions.deliberate import deliberate
            action_row = get_connection().execute(
                "SELECT * FROM actions WHERE id=?", (ticket.action_id,)
            ).fetchone()
            if action_row:
                from chloe.actions.schema import Action
                action = Action(**dict(action_row))
                verdict = await deliberate(action, context={})
                if verdict and verdict.decision != "proceed":
                    await deny(ticket_id)
                    return False
        except Exception as exc:
            log.warning("stale_redeliberate_failed", error=str(exc))

    ticket.state = "confirmed"
    kv_set(_ticket_key(ticket_id), ticket.model_dump(mode="json"))

    conn = get_connection()
    conn.execute("UPDATE actions SET state='confirmed' WHERE id=?", (ticket.action_id,))
    conn.commit()
    log.info("ticket_confirmed", ticket_id=ticket_id)
    return True


async def deny(ticket_id: str) -> bool:
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
    memory_id = ulid()
    action_row = conn.execute("SELECT * FROM actions WHERE id=?", (ticket.action_id,)).fetchone()
    intent_text = action_row["intent"] if action_row else f"Action {ticket.action_id}"
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
        VALUES ('episodic', ?, 'confirm_deny', ?, '[]', 0.8, '["held_back","denied"]', ?)
        """,
        (f"Teo denied: {intent_text}", ticket.action_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    log.info("ticket_denied", ticket_id=ticket_id)
    return True


async def expire_pending() -> int:
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

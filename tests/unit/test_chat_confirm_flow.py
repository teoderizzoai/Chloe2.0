"""End-to-end test: chat → gate → kinetic-sensitive confirm ticket → consent resolution.

Flow tested:
  1. A kinetic-sensitive verb called from chat routes through _route_kinetic_sensitive_via_gate.
  2. The gate opens a confirm ticket and stores a chat_pending_confirms row.
  3. A consent message ("yes") in the same conversation resolves the ticket.
  4. The underlying action is executed.

Uses in-memory SQLite (fresh per test) and mocks the tool registry so no real
calendar/push calls go out.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_ks_action(person_id: int = 1) -> Action:
    return Action(
        tool="calendar",
        verb="add_event",
        args={"title": "Dentist", "start": "2026-05-20T15:00:00", "end": "2026-05-20T16:00:00"},
        intent="intercept: dentist appointment on tuesday at 3pm",
        preview="Add to calendar: 'Dentist' at 2026-05-20T15:00:00",
        authorization="kinetic-sensitive",
        origin="chat",
        origin_person_id=person_id,
    )


# ---------------------------------------------------------------------------
# Gate opens confirm ticket and records chat_pending_confirms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kinetic_sensitive_opens_ticket_and_stores_chat_confirm():
    """gate.submit on a chat-origin kinetic-sensitive action stores a
    chat_pending_confirms row."""
    action = _make_ks_action()

    mock_ticket = MagicMock()
    mock_ticket.id = "TICKET001"

    from chloe.actions import gate

    with (
        patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")),
        patch("chloe.actions.gate.budget.exceeded_for", return_value=False),
        patch("chloe.actions.gate._check_ha_allowlist", new=AsyncMock(return_value=None)),
        patch("chloe.actions.gate._check_pii_filter", return_value=(False, "")),
        # should_deliberate is imported locally in gate.submit — patch the module source
        patch("chloe.actions.deliberate.should_deliberate", return_value=False),
        patch("chloe.actions.confirm.send", new=AsyncMock(return_value=mock_ticket)),
        patch("chloe.actions.gate.audit.append", new=AsyncMock()),
        patch("chloe.actions.gate.record_action"),
    ):
        result = await gate.submit(action)

    assert result.awaiting, "action should be awaiting confirmation"
    assert not result.executed
    assert result.ticket_id == "TICKET001"

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM chat_pending_confirms WHERE person_id=1 AND ticket_id='TICKET001'"
    ).fetchone()
    assert row is not None, "chat_pending_confirms row should have been written"
    assert row["state"] == "pending"
    assert row["tool"] == "calendar"
    assert row["verb"] == "add_event"


# ---------------------------------------------------------------------------
# Consent message resolves the pending confirm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consent_message_resolves_pending_confirm():
    """_maybe_resolve_pending_confirm resolves a pending ticket when user says 'yes'."""
    # Seed a chat_pending_confirms row as if the gate already opened one.
    conn = get_connection()
    conn.execute(
        """INSERT INTO chat_pending_confirms
             (person_id, action_id, ticket_id, tool, verb, preview, state)
           VALUES (1, 'ACTION001', 'TICKET001', 'calendar', 'add_event', 'Add to calendar', 'pending')"""
    )
    conn.commit()

    # Mock confirm() to return True (approved).
    with patch("chloe.actions.confirm.confirm", new=AsyncMock(return_value=True)):
        from chloe.channels.mobile_ws import _maybe_resolve_pending_confirm
        await _maybe_resolve_pending_confirm("yes", "1")

    row = conn.execute(
        "SELECT state FROM chat_pending_confirms WHERE ticket_id='TICKET001'"
    ).fetchone()
    assert row is not None
    assert row["state"] == "resolved", f"Expected resolved, got {row['state']}"


# ---------------------------------------------------------------------------
# Non-consent messages don't touch pending confirms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_consent_does_not_resolve():
    """Messages that aren't clear consent words should not resolve pending confirms."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO chat_pending_confirms
             (person_id, action_id, ticket_id, tool, verb, preview, state)
           VALUES (1, 'ACTION002', 'TICKET002', 'calendar', 'add_event', 'Add to calendar', 'pending')"""
    )
    conn.commit()

    with patch("chloe.actions.confirm.confirm", new=AsyncMock(return_value=True)) as mock_confirm:
        from chloe.channels.mobile_ws import _maybe_resolve_pending_confirm
        await _maybe_resolve_pending_confirm("how are you doing today?", "1")
        mock_confirm.assert_not_called()

    row = conn.execute(
        "SELECT state FROM chat_pending_confirms WHERE ticket_id='TICKET002'"
    ).fetchone()
    assert row["state"] == "pending", "state should not have changed for non-consent message"


# ---------------------------------------------------------------------------
# _route_kinetic_sensitive_via_gate: gate bypass check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_kinetic_sensitive_returns_awaiting():
    """_route_kinetic_sensitive_via_gate should return awaiting=True when gate
    opens a confirm ticket (i.e., kinetic-sensitive is not auto-executed)."""
    from chloe.channels.mobile_ws import _route_kinetic_sensitive_via_gate

    mock_verb = MagicMock()
    mock_verb.description_for_human = "Add calendar event"

    mock_result = MagicMock()
    mock_result.awaiting = True
    mock_result.suppressed = False
    mock_result.ticket_id = "TICKET003"
    mock_result.reason = "Awaiting confirmation (ticket TICKET003)"

    # get_registry and gate are imported inside _route_kinetic_sensitive_via_gate;
    # patch them at their source modules.
    with (
        patch("chloe.tools.registry.get_registry") as mock_reg_getter,
        patch("chloe.actions.gate.submit", new=AsyncMock(return_value=mock_result)),
    ):
        mock_reg_getter.return_value.get_verb.return_value = mock_verb

        result = await _route_kinetic_sensitive_via_gate(
            "calendar", "add_event",
            {"title": "Dentist", "start": "2026-05-20T15:00:00", "end": "2026-05-20T16:00:00"},
            "1",
        )

    assert result["awaiting"] is True
    assert result["ticket_id"] == "TICKET003"

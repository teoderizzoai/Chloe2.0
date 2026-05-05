












# A-08 · `actions/gate.py` MVP — pass-through gate

## Overview

Implement `chloe/actions/gate.py` with the MVP gate logic from PRD §8.2. Handles: leash check → budget check → auth dispatch (`free`/`intimate` execute immediately; `kinetic` execute + audit; `kinetic-sensitive` raises `NotImplementedError` for now). Deliberation is stubbed: always returns `proceed`.

## Context

The gate is the single entry point for all real-world side effects. Without it, 1.0 fires outreach ad hoc from the tick loop. With it, every proposed action is checked against the leash, the budget, and the auth ladder before any I/O occurs. This MVP excludes deliberation (D-01) and confirmation (C-07) — those are stubs.

## Implementation

```python
# chloe/actions/gate.py

import json
from datetime import datetime, timezone
from chloe.actions.schema import Action, ActionResult
from chloe.actions import audit, budget, leash as leash_mod
from chloe.tools.registry import get_registry
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger
from chloe.observability.metrics import record_action, record_held_back

log = get_logger("gate")

def _load_prefs() -> dict:
    """Load all preferences rows into a dict."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM preferences").fetchall()
    return {r["key"]: json.loads(r["value"]) for r in rows}

async def submit(action: Action) -> ActionResult:
    """
    The gate. Every Action passes through here before execution.
    Returns an ActionResult describing what happened.
    """
    now = datetime.now(timezone.utc)
    prefs = _load_prefs()

    # ── 1. Leash check ──────────────────────────────────────────────────
    violated, reason = leash_mod.violates(action, prefs, now)
    if violated:
        action.state = "suppressed_by_leash"
        await audit.append(action)
        await _store_held_back_memory(action, reason)
        record_action(action.tool, action.verb, "suppressed_by_leash")
        record_held_back("leash")
        log.info("gate_suppressed", action_id=action.id, reason=reason)
        return ActionResult(suppressed=True, reason=reason, action_id=action.id)

    # ── 2. Budget check ─────────────────────────────────────────────────
    if budget.exceeded_for(action):
        action.state = "self_aborted"
        await audit.append(action)
        await _store_held_back_memory(action, "budget_exceeded")
        record_action(action.tool, action.verb, "self_aborted")
        record_held_back("budget")
        log.info("gate_budget_exceeded", action_id=action.id)
        return ActionResult(suppressed=True, reason="budget_exceeded", action_id=action.id)

    # ── 3. Deliberation stub (always proceeds) ───────────────────────────
    # Deliberation is implemented in D-01. Until then: always proceed.

    # ── 4. Auth dispatch ─────────────────────────────────────────────────
    if action.authorization in ("free", "intimate"):
        return await _execute_and_record(action)

    if action.authorization == "kinetic":
        result = await _execute_and_record(action)
        return result

    if action.authorization == "kinetic-sensitive":
        # Confirmation not implemented until C-07
        raise NotImplementedError(
            f"kinetic-sensitive actions require confirmation (Phase C): {action.id}"
        )

    return ActionResult(suppressed=True, reason=f"unknown_auth: {action.authorization}")


async def _execute_and_record(action: Action) -> ActionResult:
    """Execute via tool registry, update action state, persist to audit."""
    registry = get_registry()

    try:
        result = await registry.execute(action.tool, action.verb, action.args)
    except Exception as e:
        action.state = "failed"
        action.error = str(e)
        await audit.append(action)
        record_action(action.tool, action.verb, "failed")
        log.error("gate_execute_failed", action_id=action.id, error=str(e))
        return ActionResult(
            executed=False, error=str(e), action_id=action.id
        )

    if result.success:
        action.state = "executed"
        action.result = result.data or {}
    else:
        action.state = "failed"
        action.error = result.error or "unknown error"

    await audit.append(action)
    record_action(action.tool, action.verb, action.state)
    log.info("gate_executed", action_id=action.id, state=action.state)

    return ActionResult(
        executed=result.success,
        suppressed=False,
        action_id=action.id,
        error=action.error,
    )


async def _store_held_back_memory(action: Action, reason: str) -> None:
    """Store an episodic memory for a held-back action."""
    from chloe.state.db import get_connection
    import json
    conn = get_connection()
    text = f"I almost {action.verb} via {action.tool}. Held back: {reason}. Intent: {action.intent}"
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, source_ref, tags, salience)
        VALUES ('episodic', ?, 'action', ?, '["held_back"]', 0.4)
        """,
        (text, action.id),
    )
    conn.commit()
```

## Thread/async safety note

`_load_prefs()` is synchronous (SQLite read). Since we're in a single asyncio thread, this is safe. If prefs change mid-execution, the next action will pick up the updated value.

## Dependencies

- A-02 (`Action`, `ActionResult`).
- A-03 (`audit.append`).
- A-04 (`leash.violates`).
- A-05 (`budget.exceeded_for`).
- A-06 (`ToolRegistry`).
- A-07 (at least one registered tool for integration tests).
- F-09 (logging).
- F-10 (metrics).

## Testing

### Unit tests — `tests/unit/test_gate.py`

```python
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close
from chloe.actions.schema import Action, ActionResult
from chloe.actions import gate
from chloe.tools.base import ToolResult

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

def make_action(auth="kinetic", tool="messages", verb="send_text"):
    return Action(
        tool=tool, verb=verb,
        intent="test intent",
        preview="test preview",
        authorization=auth,
        args={"body": "hello"},
    )

@pytest.mark.asyncio
async def test_free_action_passes_through(monkeypatch):
    a = make_action(auth="free", tool="notes", verb="read")
    mock_execute = AsyncMock(return_value=ToolResult(success=True, data={}))
    with patch("chloe.actions.gate.get_registry") as mock_reg:
        mock_reg.return_value.execute = mock_execute
        result = await gate.submit(a)
    assert result.executed
    assert not result.suppressed

@pytest.mark.asyncio
async def test_leash_blocked_action_is_suppressed(monkeypatch):
    a = make_action(auth="kinetic")
    # Set auth_ceiling to intimate so kinetic is blocked
    from chloe.state.db import get_connection
    import json
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()
    
    result = await gate.submit(a)
    assert result.suppressed
    assert not result.executed

@pytest.mark.asyncio
async def test_suppressed_action_in_db(monkeypatch):
    a = make_action(auth="kinetic")
    from chloe.state.db import get_connection
    import json
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()
    
    await gate.submit(a)
    
    row = conn.execute(
        "SELECT state FROM actions WHERE id=?", (a.id,)
    ).fetchone()
    assert row is not None
    assert row["state"] == "suppressed_by_leash"

@pytest.mark.asyncio
async def test_kinetic_sensitive_raises():
    a = make_action(auth="kinetic-sensitive")
    with pytest.raises(NotImplementedError):
        await gate.submit(a)

@pytest.mark.asyncio
async def test_held_back_memory_written(monkeypatch):
    a = make_action(auth="kinetic")
    from chloe.state.db import get_connection
    import json
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()
    
    await gate.submit(a)
    
    row = conn.execute(
        "SELECT text, tags FROM memories WHERE source='action' AND source_ref=?",
        (a.id,)
    ).fetchone()
    assert row is not None
    assert "held_back" in row["tags"]

@pytest.mark.asyncio
async def test_budget_exceeded_self_aborts(monkeypatch):
    a = make_action(auth="kinetic")
    
    with patch("chloe.actions.gate.budget.exceeded_for", return_value=True):
        result = await gate.submit(a)
    
    assert result.suppressed
    assert result.reason == "budget_exceeded"
```

## Acceptance criteria

- A `free` action passes through and `executed=True` in the result.
- A leash-blocked action returns `suppressed=True, reason="leash"`.
- The action row in DB has the correct `state`.
- `kinetic-sensitive` raises `NotImplementedError`.
- Budget exceeded returns `suppressed=True, reason="budget_exceeded"`.

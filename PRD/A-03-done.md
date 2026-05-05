# A-03 · `actions/audit.py` — audit feed

## Overview

Implement `chloe/actions/audit.py` with three functions: `append(action)` persists an `Action` to the `actions` table; `recent(n=200)` returns the last N rows as `list[Action]` ordered by `proposed_at DESC`; `feed_text(n=10)` returns a formatted string suitable for injection into the chat prompt.

## Context

The audit feed is one of the most important observability surfaces in 2.0. It appears in: the chat prompt's `audit_recent` block, the mobile app's Activity tab, the admin dashboard, and as the primary trust-building mechanism (PRD §8.6). Every action — executed, held back, suppressed — must appear here. Nothing is hidden.

## Interface

```python
# chloe/actions/audit.py

from chloe.actions.schema import Action
from typing import Any

async def append(action: Action) -> None:
    """Persist an action to the actions table. Upsert on id conflict."""
    ...

async def recent(n: int = 200) -> list[Action]:
    """Return last n actions ordered by proposed_at DESC."""
    ...

def feed_text(actions: list[Action], n: int = 10) -> str:
    """
    Format the most recent n actions as a compact text block
    suitable for injection into the Gemini chat prompt.
    """
    ...
```

## `append` implementation

```python
import json
from chloe.state.db import get_connection

async def append(action: Action) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO actions (
            id, tool, verb, args, intent, cost_tokens, cost_usd,
            authorization, preview, proposed_at, state, result,
            error, deliberation, user_response, becomes_memory_id
        ) VALUES (
            :id, :tool, :verb, :args, :intent, :cost_tokens, :cost_usd,
            :authorization, :preview, :proposed_at, :state, :result,
            :error, :deliberation, :user_response, :becomes_memory_id
        )
        ON CONFLICT(id) DO UPDATE SET
            state             = excluded.state,
            result            = excluded.result,
            error             = excluded.error,
            deliberation      = excluded.deliberation,
            user_response     = excluded.user_response,
            becomes_memory_id = excluded.becomes_memory_id,
            cost_tokens       = excluded.cost_tokens,
            cost_usd          = excluded.cost_usd
        """,
        {
            "id":               action.id,
            "tool":             action.tool,
            "verb":             action.verb,
            "args":             json.dumps(action.args),
            "intent":           action.intent,
            "cost_tokens":      action.cost_estimate.tokens,
            "cost_usd":         action.cost_estimate.usd,
            "authorization":    action.authorization,
            "preview":          action.preview,
            "proposed_at":      action.proposed_at.isoformat(),
            "state":            action.state,
            "result":           json.dumps(action.result) if action.result else None,
            "error":            action.error,
            "deliberation":     json.dumps(action.deliberation.model_dump() if action.deliberation else None),
            "user_response":    json.dumps(action.user_response.model_dump() if action.user_response else None),
            "becomes_memory_id": action.becomes_memory_id,
        },
    )
    conn.commit()
```

## `recent` implementation

```python
async def recent(n: int = 200) -> list[Action]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM actions ORDER BY proposed_at DESC LIMIT ?", (n,)
    ).fetchall()
    return [_row_to_action(row) for row in rows]

def _row_to_action(row) -> Action:
    from datetime import datetime
    from chloe.actions.schema import CostEstimate, DeliberationRecord, UserResponse, ArtifactRef
    return Action(
        id=row["id"],
        tool=row["tool"],
        verb=row["verb"],
        args=json.loads(row["args"]) if row["args"] else {},
        intent=row["intent"],
        preview=row["preview"],
        authorization=row["authorization"],
        cost_estimate=CostEstimate(
            tokens=row["cost_tokens"],
            usd=row["cost_usd"],
        ),
        proposed_at=datetime.fromisoformat(row["proposed_at"]),
        state=row["state"],
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        deliberation=DeliberationRecord(**json.loads(row["deliberation"])) if row["deliberation"] and row["deliberation"] != "null" else None,
        user_response=UserResponse(**json.loads(row["user_response"])) if row["user_response"] and row["user_response"] != "null" else None,
        becomes_memory_id=row["becomes_memory_id"],
    )
```

## `feed_text` implementation

The format must be compact enough to fit in the chat prompt context efficiently. Each line:
```
[{timestamp_short}] {tool}.{verb} → {state} — {intent_truncated}
```

```python
def feed_text(actions: list[Action], n: int = 10) -> str:
    if not actions:
        return "(no recent actions)"
    
    lines = []
    for a in actions[:n]:
        ts = a.proposed_at.strftime("%H:%M")
        intent = a.intent[:80] + "…" if len(a.intent) > 80 else a.intent
        state_symbol = {
            "executed": "✓",
            "self_aborted": "↩",
            "suppressed_by_leash": "⊘",
            "denied": "✗",
            "reverted": "↩",
            "awaiting_confirmation": "?",
        }.get(a.state, "·")
        lines.append(f"[{ts}] {a.tool}.{a.verb} {state_symbol} — {intent}")
    
    return "\n".join(lines)
```

Note: the `state_symbol` approach uses plain ASCII alternatives if the environment doesn't support unicode: `OK`, `HELD`, `BLOCKED`, `DENIED`, `REVERTED`, `PENDING`.

## Dependencies

- A-01 (`actions` table in DB).
- A-02 (`Action` schema).
- F-03 (`get_connection`).

## Testing

### Unit tests — `tests/unit/test_audit.py`

```python
import pytest
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from chloe.state.db import migrate, close
from chloe.actions.schema import Action
from chloe.actions import audit

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

def _make_action(tool="spotify", verb="queue_track", state="executed", intent="test intent"):
    return Action(
        tool=tool, verb=verb,
        args={"uri": "spotify:track:test"},
        intent=intent,
        preview=f"Would {verb}",
        authorization="kinetic",
        state=state,
    )

@pytest.mark.asyncio
async def test_append_and_recent():
    a1 = _make_action(intent="first")
    a2 = _make_action(intent="second")
    a3 = _make_action(intent="third")
    await audit.append(a1)
    await audit.append(a2)
    await audit.append(a3)
    
    results = await audit.recent(n=10)
    assert len(results) == 3
    assert results[0].intent == "third"   # DESC order

@pytest.mark.asyncio
async def test_append_upserts_on_conflict():
    a = _make_action(intent="original")
    await audit.append(a)
    a.state = "executed"
    await audit.append(a)   # upsert
    results = await audit.recent(n=10)
    assert len(results) == 1
    assert results[0].state == "executed"

@pytest.mark.asyncio
async def test_feed_text_contains_tool_verb_state():
    a = _make_action(tool="spotify", verb="queue_track", state="executed", intent="calm song")
    await audit.append(a)
    actions = await audit.recent(n=10)
    text = audit.feed_text(actions, n=3)
    assert "spotify" in text
    assert "queue_track" in text
    assert "calm song" in text

@pytest.mark.asyncio
async def test_feed_text_respects_n_limit():
    for i in range(5):
        await audit.append(_make_action(intent=f"intent {i}"))
    actions = await audit.recent(n=200)
    text = audit.feed_text(actions, n=3)
    assert text.count("\n") == 2   # 3 lines → 2 newlines

def test_feed_text_empty_list():
    text = audit.feed_text([], n=10)
    assert "no recent" in text.lower()
```

## Acceptance criteria

- Unit test: write 3 actions, call `feed_text(3)`, assert the lines contain tool + verb + state.
- `append()` upserts (calling twice with same `id` updates the state).
- `recent(n)` returns at most `n` rows, newest first.

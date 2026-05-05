# C-06 · Episodic memory creation hook on action execute

## Overview

In `gate.py`'s `_execute_and_record()`, after every successful execute: create an episodic memory whose `text` is `action.intent`, `source="action"`, `source_ref=action.id`, and `artifact_refs` contains any artifact ref the tool returned. Write the memory's `id` back to `actions.becomes_memory_id`.

## Context

Every kinetic action Chloe takes should leave a trace in her episodic memory. This closes the loop between the action layer and the memory system: future deliberation, retrieval, and self-modeling can reference what Chloe has actually done, not just what she intended. The `becomes_memory_id` back-reference on the `actions` row enables the audit feed to link to the corresponding memory record and enables the anchor-bonus retrieval system (E-06) to weight recently-acted-upon artifacts higher.

## Changes to `gate.py`

```python
# chloe/actions/gate.py

from chloe.memory.store import MemoryStore
from chloe.actions.schema import Action, ActionResult

_memory_store = MemoryStore()

async def _execute_and_record(action: Action) -> ActionResult:
    """Execute via registry, record result, create episodic memory."""
    registry = get_registry()
    result = await registry.execute(action.tool, action.verb, action.args)

    state = "executed" if result.success else "failed"
    conn = get_connection()
    conn.execute(
        "UPDATE actions SET state=?, result_json=?, executed_at=? WHERE id=?",
        (state, json.dumps(result.data), datetime.utcnow().isoformat(), action.id),
    )

    if result.success:
        artifact_refs = []
        if result.artifact_ref:
            artifact_refs = [{"kind": result.artifact_kind or "unknown", "ref": result.artifact_ref}]

        memory_id = await _create_action_memory(action, artifact_refs)
        if memory_id:
            conn.execute(
                "UPDATE actions SET becomes_memory_id=? WHERE id=?",
                (memory_id, action.id),
            )

    conn.commit()
    return ActionResult(
        executed=result.success,
        suppressed=not result.success,
        reason=result.error if not result.success else None,
    )


async def _create_action_memory(action: Action, artifact_refs: list) -> str | None:
    """Create an episodic memory for a successfully executed action."""
    import json as _json
    from chloe.state.db import get_connection
    from chloe.actions.schema import ulid

    memory_id = ulid()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO memories
                (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
            VALUES (?, 'episodic', ?, 'action', ?, ?, 1.0, '["action"]', ?)
            """,
            (
                memory_id,
                action.intent,
                action.id,
                _json.dumps(artifact_refs),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        log.info("action_memory_created", memory_id=memory_id, action_id=action.id)
        return memory_id
    except Exception as exc:
        log.warning("action_memory_failed", error=str(exc))
        return None
```

## Schema notes

The `memories` table (from `0001_init.sql`, F-04) must have:
- `artifact_refs JSON NOT NULL DEFAULT '[]'`
- `source TEXT` — value `"action"` used here
- `source_ref TEXT` — stores the `action.id` (ULID)
- `becomes_memory_id` column on `actions` table — add in migration if not already present

If `becomes_memory_id` is not in `0002_actions.sql`, add it in a new migration:

```sql
-- 0005_becomes_memory_id.sql
ALTER TABLE actions ADD COLUMN becomes_memory_id TEXT REFERENCES memories(id);
```

## Dependencies

- A-01 (`actions` table).
- A-08 (`gate.py` execute path).
- C-01 (artifact refs pattern — `result.artifact_ref`, `result.artifact_kind`).
- F-04 (`memories` table).

## Testing

### Unit tests — `tests/unit/test_gate_memory_hook.py`

```python
import pytest
import json
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions import gate
from chloe.tools.registry import ToolRegistry

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()

@pytest.mark.asyncio
async def test_successful_action_creates_memory(db, monkeypatch):
    from chloe.tools.base import ToolResult
    from unittest.mock import AsyncMock, MagicMock

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=True,
        data={"queued": "spotify:track:xyz"},
        artifact_ref="spotify:track:xyz",
        artifact_kind="spotify_track",
    ))
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = Action(
        tool="spotify", verb="queue_track",
        args={"uri": "spotify:track:xyz"},
        intent="Queue a favourite track to set the mood",
        preview="Queue Radiohead track",
        authorization="kinetic",
    )
    result = await gate.submit(action)

    assert result.executed

    # Memory row created
    row = db.execute("SELECT * FROM memories WHERE source='action'").fetchone()
    assert row is not None
    assert row["text"] == "Queue a favourite track to set the mood"
    assert row["source_ref"] == action.id

    # Artifact refs stored
    artifact_refs = json.loads(row["artifact_refs"])
    assert len(artifact_refs) == 1
    assert artifact_refs[0]["ref"] == "spotify:track:xyz"

    # actions.becomes_memory_id back-filled
    action_row = db.execute("SELECT becomes_memory_id FROM actions WHERE id=?", (action.id,)).fetchone()
    assert action_row["becomes_memory_id"] == row["id"]


@pytest.mark.asyncio
async def test_failed_action_does_not_create_memory(db, monkeypatch):
    from chloe.tools.base import ToolResult
    from unittest.mock import AsyncMock, MagicMock

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=False,
        error="Spotify token expired",
    ))
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = Action(
        tool="spotify", verb="queue_track",
        args={"uri": "spotify:track:xyz"},
        intent="Queue a track",
        preview="Queue track",
        authorization="kinetic",
    )
    await gate.submit(action)

    count = db.execute("SELECT COUNT(*) FROM memories WHERE source='action'").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_memory_has_action_tag(db, monkeypatch):
    from chloe.tools.base import ToolResult
    from unittest.mock import AsyncMock, MagicMock
    import json

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = Action(
        tool="notes", verb="append",
        args={"path": "journal.md", "text": "Today was good"},
        intent="Append a journal entry",
        preview="Append to journal",
        authorization="kinetic",
    )
    await gate.submit(action)

    row = db.execute("SELECT tags FROM memories WHERE source='action'").fetchone()
    tags = json.loads(row["tags"])
    assert "action" in tags
```

## Acceptance criteria

- After a successful `queue_track` execute, `memories` has one row with `source="action"`.
- `memories.source_ref` equals the action's ULID.
- `artifact_refs` contains the artifact the tool returned.
- `actions.becomes_memory_id` points to the new memory row.
- A failed execute creates no memory row.

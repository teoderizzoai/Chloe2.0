# C-03 · `tools/notes.py` — wire to artifact_index

## Overview

After every `create` or `append` verb in `notes.py`, call `artifact_index.register(kind="notes_doc", ref=path, snapshot=first_200_chars)`. The verbs already exist from A-11 — this step adds the artifact tracking.

## Context

The `notes` tool was built in A-11 but didn't register artifacts (A-01 existed but wasn't wired). Now that the full stack is in place (artifact_index table, action gate, artifact refs on memories), every notes write should create an artifact row so the anchor-bonus retrieval system (E-06) can find it.

## Changes to `notes.py`

```python
# Add after successful create/append in NotesTool.execute():

def _register_artifact(path: str, text: str, action_id: str) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    snapshot = text[:200] if text else ""
    conn.execute(
        """
        INSERT OR REPLACE INTO artifact_index (kind, ref, title, snapshot, created_by_action, exists_)
        VALUES ('notes_doc', ?, ?, ?, ?, 1)
        """,
        (path, path.split("/")[-1], snapshot, action_id or ""),
    )
    conn.commit()
```

Call in `execute()`:
```python
elif verb == "create":
    ...
    p.write_text(args["text"], encoding="utf-8")
    _register_artifact(args["path"], args["text"], args.get("__action_id", ""))
    return ToolResult(success=True, data={"path": args["path"]},
                     artifact_ref=args["path"], artifact_kind="notes_doc")

elif verb == "append":
    ...
    with p.open("a") as f: f.write(args["text"])
    # Update snapshot in artifact_index
    new_content = p.read_text()
    _register_artifact(args["path"], new_content, args.get("__action_id", ""))
    return ToolResult(success=True, data={"path": args["path"]},
                     artifact_ref=args["path"], artifact_kind="notes_doc")
```

## Dependencies

- A-11 (existing `NotesTool`).
- A-01 (`artifact_index` table).

## Testing

### Unit tests — `tests/unit/test_notes_artifact.py`

```python
import pytest
import asyncio
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.tools.notes import NotesTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture
def setup(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    tool = NotesTool(notes_dir=tmp_path / "notes")
    yield tool
    close()

@pytest.mark.asyncio
async def test_create_registers_artifact(setup):
    tool = setup
    await tool.execute("create", {"path": "plan.md", "text": "my plan", "__action_id": "act1"})
    
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='plan.md'").fetchone()
    assert row is not None
    assert row["kind"] == "notes_doc"
    assert "my plan" in row["snapshot"]

@pytest.mark.asyncio
async def test_append_updates_artifact(setup):
    tool = setup
    await tool.execute("create", {"path": "doc.md", "text": "original", "__action_id": "act1"})
    await tool.execute("append", {"path": "doc.md", "text": "\naddition", "__action_id": "act2"})
    
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='doc.md'").fetchone()
    assert "original" in row["snapshot"]

@pytest.mark.asyncio
async def test_read_does_not_create_artifact(setup):
    tool = setup
    await tool.execute("create", {"path": "r.md", "text": "hi"})
    
    # Clear artifacts
    conn = get_connection()
    conn.execute("DELETE FROM artifact_index")
    conn.commit()
    
    await tool.execute("read", {"path": "r.md"})
    count = conn.execute("SELECT COUNT(*) FROM artifact_index").fetchone()[0]
    assert count == 0   # read should not create artifact
```

## Acceptance criteria

- `create` → `artifact_index` has one row with `kind="notes_doc"`.
- `append` updates the existing artifact row (via `INSERT OR REPLACE`).
- `read` does not create artifact rows.

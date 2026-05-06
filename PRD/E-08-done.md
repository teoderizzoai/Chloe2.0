# E-08 · `memories` table: add `artifact_refs` column

## Overview

Write migration `0003_artifact_refs.sql` adding `artifact_refs JSON NOT NULL DEFAULT '[]'` to `memories` (if not already in `0001_init.sql`). Backfill: for every memory with `source="action"`, look up the action's artifact in `artifact_index` and copy the ref.

## Context

The `artifact_refs` column is what enables the anchor-bonus retrieval (E-06) and the connection between episodic memories and the real-world objects they reference. Without this column, memories from actions have no way to signal "this memory is about a real thing that still exists." The backfill is important for production data: existing episodic memories created by the C-06 hook need their `artifact_refs` populated retroactively.

## Migration SQL

```sql
-- chloe/state/migrations/0003_artifact_refs.sql

-- Add artifact_refs column to memories if not present
-- (ALTER TABLE is safe if this migration is guarded by the migration runner)
ALTER TABLE memories ADD COLUMN artifact_refs JSON NOT NULL DEFAULT '[]';

-- Index for efficient anchor-bonus lookups
CREATE INDEX IF NOT EXISTS idx_memories_source_ref ON memories (source, source_ref);
```

Note: SQLite does not support `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. The migration runner's idempotency guard (checking `_migrations` table) prevents re-running, so this is safe.

## Backfill script

```python
# ops/backfill_artifact_refs.py

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from chloe.state.db import migrate, get_connection
from chloe.observability.logging import get_logger

log = get_logger("backfill_artifact_refs")


def backfill(dry_run: bool = False) -> dict:
    """
    For every memory with source='action', copy the artifact ref from
    the corresponding actions row (via becomes_memory_id or source_ref).
    """
    conn = get_connection()

    # Find action memories with empty artifact_refs
    rows = conn.execute(
        """
        SELECT m.id, m.source_ref, m.artifact_refs
        FROM memories m
        WHERE m.source = 'action'
          AND (m.artifact_refs IS NULL OR m.artifact_refs = '[]')
        """
    ).fetchall()

    updated = 0
    for row in rows:
        action_id = row["source_ref"]
        if not action_id:
            continue

        # Look up the artifact from the action's result
        action_row = conn.execute(
            "SELECT result_json FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        if not action_row or not action_row["result_json"]:
            continue

        try:
            result = json.loads(action_row["result_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Extract artifact ref from result data
        artifact_refs = []

        # Check artifact_index for any refs created by this action
        art_rows = conn.execute(
            "SELECT kind, ref FROM artifact_index WHERE created_by_action=? AND exists_=1",
            (action_id,)
        ).fetchall()
        for art in art_rows:
            artifact_refs.append({"kind": art["kind"], "ref": art["ref"]})

        if not artifact_refs:
            continue

        if not dry_run:
            conn.execute(
                "UPDATE memories SET artifact_refs=? WHERE id=?",
                (json.dumps(artifact_refs), row["id"])
            )
        log.info("backfill_memory", memory_id=row["id"], refs=artifact_refs)
        updated += 1

    if not dry_run:
        conn.commit()

    return {"updated": updated, "total_checked": len(rows)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    migrate()
    report = backfill(dry_run=args.dry_run)
    print(f"Backfill {'(DRY RUN) ' if args.dry_run else ''}complete:")
    print(f"  Checked: {report['total_checked']}")
    print(f"  Updated: {report['updated']}")
```

## Dependencies

- F-03 (`state/db.py` — migration runner).
- F-04 (`memories` table).
- C-06 (episodic memory hook — populates `source="action"` memories going forward).
- A-01 (`artifact_index` table — `created_by_action`, `ref`, `exists_`).

## Testing

### Unit tests — `tests/unit/test_artifact_refs_migration.py`

```python
import pytest
import json
from pathlib import Path
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_artifact_refs_column_exists(db):
    row = db.execute("PRAGMA table_info(memories)").fetchall()
    columns = [r["name"] for r in row]
    assert "artifact_refs" in columns


def test_artifact_refs_defaults_to_empty_list(db):
    from chloe.actions.schema import ulid
    mid = ulid()
    db.execute(
        "INSERT INTO memories (id, kind, text, source, weight, tags, created_at) VALUES (?,?,?,?,?,?,?)",
        (mid, "episodic", "test memory", "test", 1.0, "[]", "2026-05-04T10:00:00")
    )
    db.commit()

    row = db.execute("SELECT artifact_refs FROM memories WHERE id=?", (mid,)).fetchone()
    assert json.loads(row["artifact_refs"]) == []


def test_backfill_populates_artifact_refs(db):
    from ops.backfill_artifact_refs import backfill
    from chloe.actions.schema import ulid

    action_id = ulid()
    memory_id = ulid()

    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, preview, authorization, state, result_json, proposed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (action_id, "spotify", "queue_track", "{}", "Queue track", "Queue",
         "kinetic", "executed", json.dumps({"queued": "spotify:track:xyz"}), "2026-05-04T10:00:00"),
    )
    db.execute(
        "INSERT INTO memories (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (memory_id, "episodic", "Queued a track", "action", action_id, "[]", 1.0, "[]", "2026-05-04T10:00:00"),
    )
    db.execute(
        "INSERT INTO artifact_index (kind, ref, title, created_by_action, exists_) VALUES (?,?,?,?,1)",
        ("spotify_track", "spotify:track:xyz", "test", action_id),
    )
    db.commit()

    report = backfill(dry_run=False)
    assert report["updated"] == 1

    row = db.execute("SELECT artifact_refs FROM memories WHERE id=?", (memory_id,)).fetchone()
    refs = json.loads(row["artifact_refs"])
    assert len(refs) == 1
    assert refs[0]["ref"] == "spotify:track:xyz"


def test_backfill_idempotent(db):
    from ops.backfill_artifact_refs import backfill
    from chloe.actions.schema import ulid

    # Already has artifact_refs populated
    memory_id = ulid()
    db.execute(
        "INSERT INTO memories (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (memory_id, "episodic", "Already done", "action", "some_action",
         json.dumps([{"kind": "spotify_track", "ref": "existing"}]), 1.0, "[]", "2026-05-04"),
    )
    db.commit()

    report = backfill(dry_run=False)
    # Should not update already-populated memories
    row = db.execute("SELECT artifact_refs FROM memories WHERE id=?", (memory_id,)).fetchone()
    refs = json.loads(row["artifact_refs"])
    assert refs[0]["ref"] == "existing"  # Unchanged
```

## Acceptance criteria

- Migration applies; `artifact_refs` column exists on `memories` table with default `'[]'`.
- Backfill script: for every `source="action"` memory with empty `artifact_refs`, populates from `artifact_index`.
- `SELECT COUNT(*) FROM memories WHERE artifact_refs != '[]'` is non-zero after backfill.
- Backfill is idempotent (already-populated rows not overwritten).

# E-02 · Migration `0004_dimensional_affect.sql` — affect_state table

## Overview

Add the `affect_state` singleton row and `affect_records` table (if not already in `0001_init.sql`). Seed with `(valence=0.0, arousal=0.4, social_pull=0.5, openness=0.6)`. The singleton ensures there is always exactly one `affect_state` row — the current live state.

## Context

Chloe 1.0 used an 8-value mood enum (e.g., `"reflective"`, `"curious"`, `"playful"`). Chloe 2.0 replaces this with a 4D continuous state: `valence` (negative↔positive emotion), `arousal` (low↔high energy), `social_pull` (withdrawn↔sociable), `openness` (closed↔receptive). The migration seeds neutral-to-warm starting values. If `affect_state` already exists from `0001_init.sql`, this migration is a no-op.

## Migration SQL

```sql
-- chloe/state/migrations/0004_dimensional_affect.sql

-- affect_state: singleton row (only one row ever exists)
CREATE TABLE IF NOT EXISTS affect_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    valence     REAL    NOT NULL DEFAULT 0.0   CHECK (valence BETWEEN -1.0 AND 1.0),
    arousal     REAL    NOT NULL DEFAULT 0.4   CHECK (arousal BETWEEN 0.0 AND 1.0),
    social_pull REAL    NOT NULL DEFAULT 0.5   CHECK (social_pull BETWEEN 0.0 AND 1.0),
    openness    REAL    NOT NULL DEFAULT 0.6   CHECK (openness BETWEEN 0.0 AND 1.0),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Seed the singleton row if not present
INSERT OR IGNORE INTO affect_state (id, valence, arousal, social_pull, openness)
VALUES (1, 0.0, 0.4, 0.5, 0.6);

-- affect_records: time-series log of affect state changes
CREATE TABLE IF NOT EXISTS affect_records (
    id          TEXT    PRIMARY KEY,
    valence     REAL    NOT NULL,
    arousal     REAL    NOT NULL,
    social_pull REAL    NOT NULL,
    openness    REAL    NOT NULL,
    label       TEXT,
    trigger     TEXT,
    recorded_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_affect_records_at ON affect_records (recorded_at DESC);
```

## Dependencies

- F-03 (`state/db.py` — migration runner).
- F-04 (`0001_init.sql` — if `affect_state` already defined there, this migration adds `INSERT OR IGNORE` only).

## Testing

### Unit tests — `tests/unit/test_affect_migration.py`

```python
import pytest
from pathlib import Path
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_affect_state_singleton_seeded(db):
    row = db.execute("SELECT * FROM affect_state WHERE id=1").fetchone()
    assert row is not None
    assert row["valence"] == 0.0
    assert row["arousal"] == 0.4
    assert row["social_pull"] == 0.5
    assert row["openness"] == 0.6


def test_affect_state_only_one_row(db):
    count = db.execute("SELECT COUNT(*) FROM affect_state").fetchone()[0]
    assert count == 1


def test_affect_state_check_constraint(db):
    """Values outside [-1,1] / [0,1] bounds must fail."""
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE affect_state SET valence=2.0 WHERE id=1")
        db.commit()


def test_affect_records_table_exists(db):
    result = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='affect_records'"
    ).fetchone()
    assert result is not None


def test_migration_idempotent(tmp_path):
    """Running migrate() twice should not raise or duplicate rows."""
    migrate(db_path=tmp_path / "test2.db", migrations_dir=MIGRATIONS_DIR)
    migrate(db_path=tmp_path / "test2.db", migrations_dir=MIGRATIONS_DIR)  # Second run

    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM affect_state").fetchone()[0]
    assert count == 1
    close()


def test_can_insert_affect_record(db):
    from chloe.actions.schema import ulid
    record_id = ulid()
    db.execute(
        "INSERT INTO affect_records (id, valence, arousal, social_pull, openness, label, trigger) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record_id, 0.3, 0.6, 0.4, 0.7, "curious", "interesting conversation")
    )
    db.commit()

    row = db.execute("SELECT * FROM affect_records WHERE id=?", (record_id,)).fetchone()
    assert row is not None
    assert row["valence"] == 0.3
    assert row["label"] == "curious"
```

## Acceptance criteria

- Migration applies cleanly via `db.migrate()`.
- `affect_state` has exactly one row with the seeded values.
- `CHECK` constraints prevent out-of-range values.
- `affect_records` table and index exist.
- Running `migrate()` twice raises no errors and does not duplicate the singleton.

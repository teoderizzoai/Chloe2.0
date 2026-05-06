# F-04 · Migration `0001_init.sql` — core tables

## Overview

Write the complete SQL for all core tables: `memories`, `chat_history`, `persons`, `person_notes`, `person_events`, `person_moments`, `person_third_parties`, `person_attachment`, `identity_traits`, `identity_contradictions`, `inner_goals`, `inner_wants`, `inner_fears`, `inner_aversions`, `inner_beliefs`, `inner_tensions`, `arcs`, `affect_records`, `ideas`, `kv`, and `affect_state`. All indexes included. File is `chloe/state/migrations/0001_init.sql`.

## Context

The existing 1.0 SQLite schema is spread across `store.py` (implicit table creation in `_create_tables()`). The 2.0 schema is a superset: it retains the core relational structure (persons, memories, traits, inner life tables) and adds new columns (`kind`, `artifact_refs`, `archived_tier` on `memories`; `affect_state` singleton; `kv` for scalars). This migration file **replaces** the ad hoc `_create_tables()` pattern.

All statements must use `CREATE TABLE IF NOT EXISTS` for idempotency (required by the migration runner's design — if a migration is re-run after a crash mid-apply, it should not fail on already-created tables).

## Full SQL

```sql
-- 0001_init.sql
-- Core tables for Chloe 2.0
-- All IF NOT EXISTS for idempotency within executescript.

-- ──────────────────────────────────────────────────────────────
-- Memories
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS memories (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  kind              TEXT NOT NULL DEFAULT 'episodic'
                      CHECK (kind IN ('episodic','semantic','autobiographical','procedural')),
  text              TEXT NOT NULL,
  source            TEXT,
  source_ref        TEXT,
  weight            REAL NOT NULL DEFAULT 1.0,
  salience          REAL NOT NULL DEFAULT 0.5,
  confidence        REAL NOT NULL DEFAULT 1.0,
  emotional_valence REAL,
  emotional_arousal REAL,
  tags              JSON NOT NULL DEFAULT '[]',
  artifact_refs     JSON NOT NULL DEFAULT '[]',
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_tier     TEXT NOT NULL DEFAULT 'hot'
                      CHECK (archived_tier IN ('hot','warm','cold'))
);
CREATE INDEX IF NOT EXISTS idx_memories_kind_created
  ON memories(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_tags
  ON memories(json_extract(tags, '$'));
CREATE INDEX IF NOT EXISTS idx_memories_artifact
  ON memories(json_extract(artifact_refs, '$[0].ref'));
CREATE INDEX IF NOT EXISTS idx_memories_source
  ON memories(source, source_ref);

-- ──────────────────────────────────────────────────────────────
-- Chat history
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_history (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  role      TEXT NOT NULL CHECK (role IN ('user','chloe','system')),
  text      TEXT NOT NULL,
  channel   TEXT NOT NULL DEFAULT 'text'
              CHECK (channel IN ('text','voice','push','discord')),
  tool_calls JSON,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_history_person_created
  ON chat_history(person_id, created_at DESC);

-- ──────────────────────────────────────────────────────────────
-- Persons
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS persons (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  name                TEXT NOT NULL,
  aliases             JSON NOT NULL DEFAULT '[]',
  warmth              REAL NOT NULL DEFAULT 50.0,
  distance            REAL NOT NULL DEFAULT 50.0,
  conflict_level      REAL NOT NULL DEFAULT 0.0,
  conversation_count  INTEGER NOT NULL DEFAULT 0,
  last_contact        TIMESTAMP,
  response_hours      REAL,
  attachment_pattern  TEXT,
  impression          TEXT,
  trait_profile       JSON NOT NULL DEFAULT '{}',
  messaging_disabled  BOOLEAN NOT NULL DEFAULT 0,
  quiet_until         TIMESTAMP,
  work_domains        JSON NOT NULL DEFAULT '[]',
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_notes (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  text      TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  title     TEXT NOT NULL,
  date      TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_moments (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  text      TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_third_parties (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id  INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  relation   TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS person_attachment (
  person_id  INTEGER PRIMARY KEY REFERENCES persons(id) ON DELETE CASCADE,
  style      TEXT,
  notes      TEXT,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ──────────────────────────────────────────────────────────────
-- Identity
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS identity_traits (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  name                TEXT NOT NULL UNIQUE,
  weight              REAL NOT NULL DEFAULT 0.3,
  status              TEXT NOT NULL DEFAULT 'emerging'
                        CHECK (status IN ('emerging','active','core','archived','contradicted')),
  evidence_memory_ids JSON NOT NULL DEFAULT '[]',
  behavioral_profile  TEXT,
  setback_count       INTEGER NOT NULL DEFAULT 0,
  setback_notes       JSON NOT NULL DEFAULT '[]',
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS identity_contradictions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  trait_a     INTEGER REFERENCES identity_traits(id),
  trait_b     INTEGER REFERENCES identity_traits(id),
  description TEXT,
  detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolution  TEXT
);

-- ──────────────────────────────────────────────────────────────
-- Inner life
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inner_goals (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  name                TEXT NOT NULL,
  why                 TEXT,
  target_artifact_ref TEXT,
  deadline            TIMESTAMP,
  progress            REAL NOT NULL DEFAULT 0.0,
  pressure            REAL NOT NULL DEFAULT 0.0,
  status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','paused','done','failed','stale')),
  last_action_at      TIMESTAMP,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inner_wants (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  text         TEXT NOT NULL,
  tags         JSON NOT NULL DEFAULT '[]',
  pressure     REAL NOT NULL DEFAULT 0.0,
  pressure_since REAL NOT NULL DEFAULT 0.0,
  subtype      TEXT,
  resolved     BOOLEAN NOT NULL DEFAULT 0,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inner_fears (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  text       TEXT NOT NULL,
  tags       JSON NOT NULL DEFAULT '[]',
  pressure   REAL NOT NULL DEFAULT 0.0,
  resolved   BOOLEAN NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inner_aversions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  text       TEXT NOT NULL,
  tags       JSON NOT NULL DEFAULT '[]',
  resolved   BOOLEAN NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inner_beliefs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  text        TEXT NOT NULL,
  confidence  REAL NOT NULL DEFAULT 1.0,
  tags        JSON NOT NULL DEFAULT '[]',
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS inner_tensions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  text       TEXT NOT NULL,
  tags       JSON NOT NULL DEFAULT '[]',
  pressure   REAL NOT NULL DEFAULT 0.0,
  resolved   BOOLEAN NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS arcs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL
                CHECK (kind IN ('melancholic_stretch','restless_phase','curious_spell','withdrawn_period')),
  intensity   REAL NOT NULL DEFAULT 0.3,
  duration_h  REAL NOT NULL DEFAULT 0.0,
  active      BOOLEAN NOT NULL DEFAULT 1,
  started_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at    TIMESTAMP
);

-- ──────────────────────────────────────────────────────────────
-- Affect
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS affect_records (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  intensity     REAL NOT NULL,
  valence_delta REAL,
  arousal_delta REAL,
  trigger       TEXT,
  residue       REAL NOT NULL DEFAULT 0.0,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS affect_state (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  valence     REAL NOT NULL DEFAULT 0.0,
  arousal     REAL NOT NULL DEFAULT 0.4,
  social_pull REAL NOT NULL DEFAULT 0.5,
  openness    REAL NOT NULL DEFAULT 0.6,
  updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO affect_state (id, valence, arousal, social_pull, openness)
  VALUES (1, 0.0, 0.4, 0.5, 0.6);

-- ──────────────────────────────────────────────────────────────
-- Ideas
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ideas (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  text       TEXT NOT NULL,
  tags       JSON NOT NULL DEFAULT '[]',
  complete   BOOLEAN NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ──────────────────────────────────────────────────────────────
-- Interest Garden
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS interest_garden (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  label            TEXT NOT NULL UNIQUE,
  why              TEXT,
  intensity        REAL NOT NULL DEFAULT 0.3,
  artifact_refs    JSON NOT NULL DEFAULT '[]',
  last_engaged_at  TIMESTAMP,
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ──────────────────────────────────────────────────────────────
-- KV store
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kv (
  key        TEXT PRIMARY KEY,
  value      JSON NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

## What this migration does NOT include

The following tables are added in later migration files:
- `actions`, `artifact_index` → `0002_actions.sql` (Phase A)
- `preferences`, `budgets` → `0002_actions.sql` (Phase A)

## Dependencies

- F-03 (migration runner must exist to apply this file).

## Testing

### Verification tests — `tests/unit/test_migration_0001.py`

```python
import pytest
from pathlib import Path
from chloe.state.db import migrate, get_connection, close

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def reset():
    yield
    close()

def test_all_tables_created(tmp_path):
    n = migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    assert n >= 1
    conn = get_connection(tmp_path / "test.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    required = {
        "memories", "chat_history", "persons", "person_notes",
        "person_events", "person_moments", "person_third_parties",
        "person_attachment", "identity_traits", "identity_contradictions",
        "inner_goals", "inner_wants", "inner_fears", "inner_aversions",
        "inner_beliefs", "inner_tensions", "arcs", "affect_records",
        "affect_state", "ideas", "interest_garden", "kv",
    }
    assert required.issubset(tables), f"Missing: {required - tables}"

def test_affect_state_seeded(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    row = conn.execute("SELECT * FROM affect_state WHERE id=1").fetchone()
    assert row is not None
    assert row["valence"] == 0.0
    assert row["arousal"] == 0.4

def test_migration_idempotent(tmp_path):
    db = tmp_path / "test.db"
    migrate(db_path=db, migrations_dir=MIGRATIONS_DIR)
    close()
    n2 = migrate(db_path=db, migrations_dir=MIGRATIONS_DIR)
    assert n2 == 0   # already applied
```

Also run `sqlite3 chloe.db ".tables"` against a fresh DB to manually verify the list.

## Acceptance criteria

- `migrate()` applies `0001_init.sql`.
- `sqlite3 chloe.db ".tables"` shows all 22+ tables listed above.
- `affect_state` row with `id=1` has the seeded default values.
- Applying the migration twice produces no error.

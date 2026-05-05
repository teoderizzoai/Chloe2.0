# A-01 · Migration `0002_actions.sql` — actions + artifact_index + preferences + budgets

## Overview

Write the SQL for tables: `actions`, `artifact_index`, `preferences`, `budgets`. File: `chloe/state/migrations/0002_actions.sql`. Seed the three `budgets` rows with `INSERT OR IGNORE`. These tables are the foundation of the entire action layer.

## Context

The current 1.0 codebase has no explicit action log. Every outreach (Discord DM, autonomous message) fires directly without any record of what was proposed, checked, or suppressed. 2.0 requires that **every** side effect passes through the gate and every gate decision is persisted to `actions`. The `artifact_index` tracks real-world artifacts Chloe creates (tracks, events, notes). `preferences` stores the leash configuration. `budgets` tracks LLM spend across three time windows.

## SQL

```sql
-- 0002_actions.sql
-- Action layer tables for Chloe 2.0 Phase A

-- ──────────────────────────────────────────────────────────────
-- Actions (audit log + gate state machine)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS actions (
  id            TEXT PRIMARY KEY,
  tool          TEXT NOT NULL,
  verb          TEXT NOT NULL,
  args          JSON NOT NULL DEFAULT '{}',
  intent        TEXT NOT NULL,
  cost_tokens   INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL DEFAULT 0.0,
  authorization TEXT NOT NULL
                  CHECK (authorization IN ('free','intimate','kinetic','kinetic-sensitive')),
  preview       TEXT NOT NULL,
  proposed_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  state         TEXT NOT NULL DEFAULT 'proposed'
                  CHECK (state IN (
                    'proposed','deliberating','self_aborted','suppressed_by_leash',
                    'awaiting_confirmation','confirmed','denied','executed','failed','reverted'
                  )),
  result        JSON,
  error         TEXT,
  deliberation  JSON,
  user_response JSON,
  becomes_memory_id INTEGER REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_actions_state
  ON actions(state);
CREATE INDEX IF NOT EXISTS idx_actions_tool_proposed
  ON actions(tool, proposed_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_proposed_at
  ON actions(proposed_at DESC);

-- ──────────────────────────────────────────────────────────────
-- Artifact index
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS artifact_index (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  kind                TEXT NOT NULL
                        CHECK (kind IN (
                          'spotify_track','spotify_playlist','gmail_thread',
                          'calendar_event','notes_doc','url','bookmark','reminder'
                        )),
  ref                 TEXT NOT NULL UNIQUE,
  title               TEXT,
  snapshot            TEXT,
  created_by_action   TEXT REFERENCES actions(id),
  last_verified_at    TIMESTAMP,
  exists_             BOOLEAN NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_artifacts_kind
  ON artifact_index(kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_exists
  ON artifact_index(exists_);

-- ──────────────────────────────────────────────────────────────
-- Preferences (leash configuration)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preferences (
  key        TEXT PRIMARY KEY,
  value      JSON NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed default preferences
INSERT OR IGNORE INTO preferences (key, value) VALUES
  ('quiet_hours',        '{"start":"23:00","end":"08:00","timezone":"Europe/Rome","exempt_verbs":["self.*"]}'),
  ('dont_touch',         '{"gmail_labels":[],"spotify_playlists":[],"notes_folders":[]}'),
  ('auth_ceiling',       '"kinetic"'),
  ('spending_cap_usd_day', '1.50'),
  ('focus_mode',         'false'),
  ('away_mode',          'false'),
  ('web_search_blocklist','[]'),
  ('gmail_footer',       'true'),
  ('ha_allowlist',       '[]'),
  ('ha_blocklist',       '["lock.*","alarm.*","switch.oven*"]');

-- ──────────────────────────────────────────────────────────────
-- Budgets
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budgets (
  window    TEXT PRIMARY KEY CHECK (window IN ('today','this_hour','this_week')),
  tokens    INTEGER NOT NULL DEFAULT 0,
  usd       REAL    NOT NULL DEFAULT 0.0,
  reset_at  TIMESTAMP NOT NULL
);

INSERT OR IGNORE INTO budgets (window, tokens, usd, reset_at) VALUES
  ('today',     0, 0.0, date('now', '+1 day')),
  ('this_hour', 0, 0.0, datetime('now', '+1 hour')),
  ('this_week', 0, 0.0, date('now', 'weekday 1'));
```

## Dependencies

- F-03 (migration runner).
- F-04 (`0001_init.sql` must be applied first; `actions` references `memories`).

## Testing

### Unit tests — `tests/unit/test_migration_0002.py`

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
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "actions" in tables
    assert "artifact_index" in tables
    assert "preferences" in tables
    assert "budgets" in tables

def test_budgets_seeded(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    windows = {r[0] for r in conn.execute("SELECT window FROM budgets").fetchall()}
    assert windows == {"today", "this_hour", "this_week"}

def test_preferences_seeded(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    keys = {r[0] for r in conn.execute("SELECT key FROM preferences").fetchall()}
    assert "quiet_hours" in keys
    assert "auth_ceiling" in keys
    assert "spending_cap_usd_day" in keys

def test_actions_state_constraint(tmp_path):
    import pytest, sqlite3
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    # Insert a person first (FK from chat_history, not actions — but persons needed)
    conn.execute("INSERT INTO persons (name) VALUES ('Teo')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO actions (id, tool, verb, intent, authorization, preview, state)
            VALUES ('test-id', 'spotify', 'queue_track', 'test', 'kinetic', 'preview', 'invalid_state')
        """)
        conn.commit()

def test_actions_authorization_constraint(tmp_path):
    import pytest, sqlite3
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO actions (id, tool, verb, intent, authorization, preview)
            VALUES ('test-id2', 'spotify', 'queue_track', 'test', 'superkinetic', 'preview')
        """)
        conn.commit()

def test_chloe_actions_counter_can_increment():
    """After applying migrations, the Prometheus counter must be incrementable."""
    from chloe.observability.metrics import record_action
    record_action("spotify", "queue_track", "executed")  # Should not raise
```

## Acceptance criteria

- Migration applies cleanly after `0001_init.sql`.
- `actions`, `artifact_index`, `preferences`, `budgets` tables exist.
- Three `budgets` rows seeded (today, this_hour, this_week).
- Default preference keys seeded.
- `state` and `authorization` CHECK constraints enforce valid values.

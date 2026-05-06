# F-03 · `state/db.py` — SQLite WAL connection and migration runner

## Overview

Implement `chloe/state/db.py`. Opens `chloe.db` with `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`. Provides a `migrate()` function that reads `state/migrations/*.sql` files ordered by name, tracks applied migrations in a `_migrations` table, and applies pending ones idempotently at boot.

## Context

The current codebase uses `store.py` which creates the SQLite connection ad hoc and manages the schema inline. 2.0 introduces a proper migration runner so schema changes are version-controlled, auditable, and applied automatically at boot. The `_migrations` table pattern is borrowed from lightweight migration frameworks like Flyway/golang-migrate but implemented in pure Python.

## Interface

```python
# chloe/state/db.py

import sqlite3
from pathlib import Path

def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return the open WAL-mode connection, creating it if needed."""
    ...

def migrate(db_path: Path | None = None, migrations_dir: Path | None = None) -> int:
    """
    Apply all pending migrations from migrations_dir/*.sql in lexicographic order.
    Returns the number of migrations applied.
    Idempotent: calling twice with no new .sql files applies 0 migrations.
    """
    ...

def close() -> None:
    """Close the connection (used in tests and shutdown)."""
    ...
```

## Detailed behaviour

### Connection setup

```python
conn = sqlite3.connect(db_path, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, better performance
conn.row_factory = sqlite3.Row              # row access by column name
```

The connection is a module-level singleton. `get_connection()` returns the same object on repeated calls. This is intentional — SQLite with WAL supports concurrent readers; a single writer connection is the correct pattern for a single-process app.

### Migration runner

```python
def migrate(db_path=None, migrations_dir=None) -> int:
    conn = get_connection(db_path)
    
    # Ensure the tracking table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            filename  TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    
    # Find all .sql files, sorted lexicographically
    mdir = migrations_dir or Path(__file__).parent / "migrations"
    sql_files = sorted(mdir.glob("*.sql"))
    
    applied = 0
    for f in sql_files:
        row = conn.execute(
            "SELECT 1 FROM _migrations WHERE filename = ?", (f.name,)
        ).fetchone()
        if row:
            continue   # already applied
        
        sql = f.read_text(encoding="utf-8")
        conn.executescript(sql)    # executescript commits automatically
        conn.execute(
            "INSERT INTO _migrations (filename) VALUES (?)", (f.name,)
        )
        conn.commit()
        applied += 1
    
    return applied
```

**Important:** `executescript` is used (not `execute`) because migration files may contain multiple statements. `executescript` also issues an implicit `COMMIT` before running, which is the correct behaviour.

### Default paths

- `db_path` defaults to `get_settings().chloe_db_path`.
- `migrations_dir` defaults to `Path(__file__).parent / "migrations"`.

Both can be overridden in tests to point at in-memory or temp-file databases.

## Dependencies

- F-01 (package structure).
- F-02 (config, for default `db_path`).

## Testing

### Unit tests — `tests/unit/test_db.py`

```python
import pytest
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from chloe.state.db import migrate, get_connection, close

@pytest.fixture(autouse=True)
def reset_connection():
    yield
    close()   # ensure each test starts with a fresh connection

def test_migrate_with_no_sql_files_applies_zero(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    db_path = tmp_path / "test.db"
    n = migrate(db_path=db_path, migrations_dir=migrations_dir)
    assert n == 0

def test_migrations_table_created(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    db_path = tmp_path / "test.db"
    migrate(db_path=db_path, migrations_dir=migrations_dir)
    conn = get_connection(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "_migrations" in tables

def test_migrate_idempotent(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_test.sql").write_text(
        "CREATE TABLE IF NOT EXISTS foo (id INTEGER PRIMARY KEY);"
    )
    db_path = tmp_path / "test.db"
    n1 = migrate(db_path=db_path, migrations_dir=migrations_dir)
    assert n1 == 1
    close()
    n2 = migrate(db_path=db_path, migrations_dir=migrations_dir)
    assert n2 == 0   # already applied

def test_wal_mode_set(tmp_path):
    db_path = tmp_path / "test.db"
    migrate(db_path=db_path, migrations_dir=tmp_path / "empty")
    conn = get_connection(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"

def test_foreign_keys_on(tmp_path):
    db_path = tmp_path / "test.db"
    (tmp_path / "migrations").mkdir()
    migrate(db_path=db_path, migrations_dir=tmp_path / "migrations")
    conn = get_connection(db_path)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1

def test_multiple_migrations_applied_in_order(tmp_path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_a.sql").write_text(
        "CREATE TABLE IF NOT EXISTS a (id INTEGER PRIMARY KEY);"
    )
    (migrations_dir / "0002_b.sql").write_text(
        "CREATE TABLE IF NOT EXISTS b (id INTEGER PRIMARY KEY);"
    )
    db_path = tmp_path / "test.db"
    n = migrate(db_path=db_path, migrations_dir=migrations_dir)
    assert n == 2
    conn = get_connection(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "a" in tables
    assert "b" in tables
```

## Acceptance criteria

- `migrate()` called twice with no `.sql` files raises no errors and writes 0 rows to `_migrations` (second call, since no files apply).
- `migrate()` with 1 new `.sql` file inserts 1 row to `_migrations`.
- Second call with same file inserts 0 new rows.
- `PRAGMA journal_mode` returns `"wal"`.
- `PRAGMA foreign_keys` returns `1`.

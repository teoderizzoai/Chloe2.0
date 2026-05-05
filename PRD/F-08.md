# F-08 · `state/kv.py` — key-value scalar store

## Overview

Implement `chloe/state/kv.py` providing typed `get(key, default)` / `set(key, value)` / `delete(key)` over the `kv` SQLite table. Serialises values to JSON. This replaces the ad hoc `chloe_state.json` pattern for all scalar/simple-list state.

## Context

The existing 1.0 codebase maintains `data/chloe_state.json` for atomic scalars that don't fit the relational model: vitals, current activity, mood, arc state, pending outreach, last journal date, etc. The JSON file has a known weakness: writes are non-atomic (a crash mid-write loses state). Moving to SQLite `kv` gives atomicity (SQLite transactions), WAL durability, and eliminates the dual-persistence pattern (SQLite + JSON).

The `kv` table was defined in F-04 (`0001_init.sql`).

## Interface

```python
# chloe/state/kv.py

from typing import Any, TypeVar

T = TypeVar("T")

def get(key: str, default: T = None) -> T:
    """
    Read a value from kv. Returns default if key does not exist.
    Deserialises from JSON. Preserves Python types: str, int, float, list, dict, None, bool.
    """
    ...

def set(key: str, value: Any) -> None:
    """
    Upsert a value in kv. Serialises to JSON.
    Updates updated_at timestamp.
    """
    ...

def delete(key: str) -> None:
    """
    Delete a key. No-op if key does not exist.
    """
    ...

def get_all() -> dict[str, Any]:
    """Return all kv pairs as a dict. Used for debug/export."""
    ...
```

## Known keys (documentation)

These are the primary keys used across the codebase. Document them as constants or in a comment block:

```python
# Known kv keys (not enforced — just documented)
KV_VITALS          = "vitals"              # dict: energy, social_battery, curiosity, focus, inspiration
KV_CURRENT_ACTIVITY = "current_activity"  # str
KV_MOOD_LABEL      = "mood_label"         # str (legacy 1.0, replaced by affect dims in E-05)
KV_AFFECT_LABEL_CACHE = "affect_label_cache"  # str: lazy affect label
KV_AFFECT_LABEL_CACHED_AT = "affect_label_cached_at"  # iso timestamp
KV_LAST_REFLECT_AT = "last_reflect_at"    # iso timestamp
KV_LAST_CHAT_SEEN  = "last_chat_seen"     # iso timestamp
KV_CACHE_NAME      = "gemini_cache_name"  # str: Gemini cached content name
KV_OPP_VECTOR      = "opportunity_vector" # dict: last OpportunityVector
KV_OPP_VECTOR_AT   = "opportunity_vector_at"  # iso timestamp
KV_SHADOW_DECISIONS = "shadow_decisions"  # list: phase D shadow log
KV_CURRENT_ACTIVITY_DETAIL = "current_activity_detail"  # str: for "Now" tab
```

## Implementation

```python
import json
from chloe.state.db import get_connection

def get(key: str, default=None):
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    return json.loads(row[0])

def set(key: str, value) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO kv (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, json.dumps(value, default=str)),
    )
    conn.commit()

def delete(key: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    conn.commit()

def get_all() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM kv").fetchall()
    return {r[0]: json.loads(r[1]) for r in rows}
```

## Thread safety note

The underlying `get_connection()` returns a single module-level `sqlite3.Connection`. In the asyncio single-threaded model, concurrent coroutines do not interleave at the Python level during SQLite operations, so no locking is needed for the async use case. If background threads are introduced later (they shouldn't be), revisit.

## Dependencies

- F-03 (`db.py` for `get_connection()`).
- F-04 (`0001_init.sql` must have been applied to have the `kv` table).

## Testing

### Unit tests — `tests/unit/test_kv.py`

```python
import pytest
from pathlib import Path
from chloe.state.db import migrate, close
from chloe.state import kv

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Each test gets a fresh in-memory-ish DB."""
    db_path = tmp_path / "test.db"
    migrate(db_path=db_path, migrations_dir=MIGRATIONS_DIR)
    # Patch get_connection to use tmp db
    import chloe.state.db as db_mod
    original = db_mod._connection
    db_mod._connection = None
    db_mod._db_path_override = db_path
    yield
    close()
    db_mod._connection = original
    db_mod._db_path_override = None

def test_get_returns_default_when_missing():
    assert kv.get("nonexistent", "fallback") == "fallback"
    assert kv.get("nonexistent") is None

def test_set_and_get_string():
    kv.set("foo", "bar")
    assert kv.get("foo") == "bar"

def test_set_and_get_int():
    kv.set("num", 42)
    assert kv.get("num") == 42

def test_set_and_get_float():
    kv.set("pi", 3.14)
    assert abs(kv.get("pi") - 3.14) < 0.001

def test_set_and_get_list():
    kv.set("items", [1, "two", 3.0])
    result = kv.get("items")
    assert result == [1, "two", 3.0]

def test_set_and_get_dict():
    data = {"a": 1, "b": [2, 3], "c": None}
    kv.set("obj", data)
    assert kv.get("obj") == data

def test_set_and_get_none():
    kv.set("nope", None)
    assert kv.get("nope") is None

def test_set_overwrites():
    kv.set("key", "first")
    kv.set("key", "second")
    assert kv.get("key") == "second"

def test_delete_removes_key():
    kv.set("to_delete", "value")
    kv.delete("to_delete")
    assert kv.get("to_delete") is None

def test_delete_nonexistent_is_noop():
    kv.delete("never_existed")  # should not raise

def test_get_all_returns_all():
    kv.set("a", 1)
    kv.set("b", 2)
    all_kv = kv.get_all()
    assert all_kv["a"] == 1
    assert all_kv["b"] == 2
```

## Acceptance criteria

- Unit test suite green: `str`, `int`, `float`, `list`, `dict`, `None` all round-trip correctly.
- `delete` is a no-op on a non-existent key.
- `set` followed by `set` with same key returns the second value.

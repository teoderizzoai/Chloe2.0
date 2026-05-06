# E-01 · One-shot migration: `chloe_state.json` → `kv` table

## Overview

Write `ops/migrate_json_to_kv.py` that reads `chloe_state.json`, maps each scalar key to the correct `kv` key, and inserts via `kv.set()`. Idempotent (skips already-set keys). Deletes the JSON file on success.

## Context

Chloe 1.0 stored mutable runtime state in `chloe_state.json` — things like the last outreach timestamp, the current mood, conversation-turn counter, and backoff flags. In 2.0 all of these live in the `kv` SQLite table. This migration script runs once on the production server to transplant that state before 2.0 goes live, ensuring no runtime state is lost during the cutover.

## Key mapping

The mapping below covers the known 1.0 state keys. Unknown keys are prefixed with `legacy:` and preserved rather than discarded.

```python
# ops/migrate_json_to_kv.py

KEY_MAP = {
    # 1.0 key → 2.0 kv key
    "last_outreach_ts":       "last_outreach_ts",
    "last_chat_seen":         "last_chat_seen",
    "current_mood":           "affect_label_cache",   # closest equivalent
    "turn_count":             "turn_count",
    "backoff_until":          "backoff_until",
    "daily_message_count":    "daily_message_count",
    "spotify_is_playing":     "spotify_is_playing",
    "current_track_uri":      "current_track_uri",
    "cache_name":             "gemini_cache_name",
}
```

## Implementation

```python
#!/usr/bin/env python3
# ops/migrate_json_to_kv.py

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parents[1]))

from chloe.state.db import migrate as db_migrate, get_connection
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("migrate_json_to_kv")

KEY_MAP = {
    "last_outreach_ts":       "last_outreach_ts",
    "last_chat_seen":         "last_chat_seen",
    "current_mood":           "affect_label_cache",
    "turn_count":             "turn_count",
    "backoff_until":          "backoff_until",
    "daily_message_count":    "daily_message_count",
    "spotify_is_playing":     "spotify_is_playing",
    "current_track_uri":      "current_track_uri",
    "cache_name":             "gemini_cache_name",
}

SCALAR_TYPES = (str, int, float, bool, type(None))


def migrate_json_to_kv(json_path: Path, dry_run: bool = False) -> dict:
    """
    Read chloe_state.json and migrate scalar keys to kv table.
    Returns a report dict with counts.
    """
    if not json_path.exists():
        log.info("json_not_found", path=str(json_path))
        return {"skipped": True, "reason": "file not found"}

    with json_path.open() as f:
        state = json.load(f)

    migrated = 0
    skipped = 0
    unknown = 0

    for old_key, value in state.items():
        new_key = KEY_MAP.get(old_key)
        if new_key is None:
            new_key = f"legacy:{old_key}"
            unknown += 1

        existing = kv_get(new_key)
        if existing is not None:
            log.debug("kv_key_exists_skip", key=new_key)
            skipped += 1
            continue

        if not dry_run:
            kv_set(new_key, value)
        log.info("kv_migrated", old_key=old_key, new_key=new_key,
                 value_type=type(value).__name__)
        migrated += 1

    report = {"migrated": migrated, "skipped": skipped, "unknown_keys": unknown}

    if not dry_run and migrated > 0:
        backup_path = json_path.with_suffix(".json.bak")
        json_path.rename(backup_path)
        log.info("json_backed_up", backup=str(backup_path))

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Migrate chloe_state.json to kv table")
    parser.add_argument("--json-path", default="chloe_state.json",
                        help="Path to chloe_state.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be migrated without writing")
    args = parser.parse_args()

    settings = get_settings()
    db_migrate()

    json_path = Path(args.json_path)
    report = migrate_json_to_kv(json_path, dry_run=args.dry_run)

    print(f"Migration {'(DRY RUN) ' if args.dry_run else ''}complete:")
    print(f"  Migrated:     {report.get('migrated', 0)}")
    print(f"  Skipped:      {report.get('skipped', 0)}")
    print(f"  Unknown keys: {report.get('unknown_keys', 0)}")
```

## Dependencies

- F-03 (`state/db.py` — `migrate()` and connection).
- F-08 (`state/kv.py` — `get`, `set`).

## Testing

### Unit tests — `tests/unit/test_migrate_json_to_kv.py`

```python
import pytest
import json
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from ops.migrate_json_to_kv import migrate_json_to_kv

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def test_migrates_known_keys(tmp_path):
    from chloe.state.kv import get as kv_get

    state = {
        "last_outreach_ts": "2026-05-01T10:00:00",
        "current_mood": "curious",
        "turn_count": 42,
    }
    json_path = tmp_path / "chloe_state.json"
    json_path.write_text(json.dumps(state))

    report = migrate_json_to_kv(json_path)
    assert report["migrated"] == 3
    assert kv_get("last_outreach_ts") == "2026-05-01T10:00:00"
    assert kv_get("affect_label_cache") == "curious"
    assert kv_get("turn_count") == 42


def test_idempotent_skips_existing(tmp_path):
    from chloe.state.kv import set as kv_set, get as kv_get

    kv_set("last_outreach_ts", "existing_value")
    state = {"last_outreach_ts": "new_value"}
    json_path = tmp_path / "chloe_state.json"
    json_path.write_text(json.dumps(state))

    report = migrate_json_to_kv(json_path)
    assert report["skipped"] == 1
    assert kv_get("last_outreach_ts") == "existing_value"  # Not overwritten


def test_unknown_keys_prefixed_with_legacy(tmp_path):
    from chloe.state.kv import get as kv_get

    state = {"my_custom_key": "some value"}
    json_path = tmp_path / "chloe_state.json"
    json_path.write_text(json.dumps(state))

    report = migrate_json_to_kv(json_path)
    assert report["unknown_keys"] == 1
    assert kv_get("legacy:my_custom_key") == "some value"


def test_json_backed_up_after_migration(tmp_path):
    state = {"turn_count": 5}
    json_path = tmp_path / "chloe_state.json"
    json_path.write_text(json.dumps(state))

    migrate_json_to_kv(json_path)
    assert not json_path.exists()
    assert (tmp_path / "chloe_state.json.bak").exists()


def test_dry_run_does_not_write_or_delete(tmp_path):
    from chloe.state.kv import get as kv_get

    state = {"turn_count": 99}
    json_path = tmp_path / "chloe_state.json"
    json_path.write_text(json.dumps(state))

    report = migrate_json_to_kv(json_path, dry_run=True)
    assert report["migrated"] == 1
    assert json_path.exists()  # Not deleted in dry-run
    assert kv_get("turn_count") is None  # Not written


def test_missing_json_returns_skip():
    report = migrate_json_to_kv(Path("/nonexistent/chloe_state.json"))
    assert report.get("skipped") is True
```

## Acceptance criteria

- Run on a copy of production DB: all known keys present in `kv` after migration.
- Second run: all keys skipped (idempotent).
- Unknown keys preserved as `legacy:*`.
- `chloe_state.json` backed up to `.json.bak` after successful migration.
- `--dry-run` shows what would be migrated without writing.
- Server restarts with no JSON file: all scalars accessible via `kv.get()`.

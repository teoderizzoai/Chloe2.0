#!/usr/bin/env python3
"""E-01 · One-shot migration: chloe_state.json → kv table.

Reads scalar keys from chloe_state.json, writes each to the kv table via
kv.set() (skips any key already present), then deletes the json file.
Idempotent: safe to run multiple times.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from chloe.state.db import migrate, get_connection  # noqa: E402 — after sys.path

_KEY_MAP = {
    "mood_label": "mood_label",
    "last_chat_seen": "last_chat_seen",
    "current_activity": "current_activity",
    "affect_label_cache": "affect_label_cache",
    "voice_drift_notes": "voice_drift_notes",
}


def run(json_path: Path = Path("chloe_state.json")) -> int:
    if not json_path.exists():
        print(f"{json_path} not found — nothing to migrate.")
        return 0

    migrate()

    from chloe.state import kv

    state: dict = json.loads(json_path.read_text(encoding="utf-8"))
    migrated = 0
    skipped = 0

    for src_key, dst_key in _KEY_MAP.items():
        if src_key not in state:
            continue
        existing = kv.get(dst_key)
        if existing is not None:
            skipped += 1
            continue
        kv.set(dst_key, state[src_key])
        migrated += 1
        print(f"  migrated: {src_key!r} → kv[{dst_key!r}]")

    print(f"\nMigrated {migrated} keys, skipped {skipped} already-set keys.")
    json_path.unlink()
    print(f"Deleted {json_path}.")
    return migrated


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("chloe_state.json")
    run(path)

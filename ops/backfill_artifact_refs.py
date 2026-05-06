#!/usr/bin/env python3
"""E-08 · Backfill artifact_refs on memories whose source='action'.

For every memory with source='action' and empty artifact_refs, look up the
action's artifact in artifact_index and copy the ref into the memory row.
Idempotent: skips memories that already have artifact_refs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from chloe.state.db import migrate, get_connection  # noqa: E402


def run() -> int:
    migrate()
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT id, source_ref FROM memories
        WHERE source = 'action'
          AND (artifact_refs IS NULL OR artifact_refs = '[]')
        """
    ).fetchall()

    updated = 0
    for row in rows:
        mem_id = row["id"]
        action_id = row["source_ref"]
        if not action_id:
            continue

        artifact = conn.execute(
            "SELECT kind, ref, title FROM artifact_index WHERE created_by_action = ?",
            (action_id,),
        ).fetchone()
        if artifact is None:
            continue

        refs = [{"kind": artifact["kind"], "ref": artifact["ref"],
                 "snapshot": artifact["title"] or ""}]
        conn.execute(
            "UPDATE memories SET artifact_refs = ? WHERE id = ?",
            (json.dumps(refs), mem_id),
        )
        updated += 1

    conn.commit()
    print(f"Backfilled {updated} memories with artifact_refs.")
    return updated


if __name__ == "__main__":
    run()

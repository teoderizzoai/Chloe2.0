from __future__ import annotations

import json
from chloe.state.db import get_connection


def set(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO kv (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, json.dumps(value)),
    )
    conn.commit()


def get(key: str) -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return json.loads(row[0])

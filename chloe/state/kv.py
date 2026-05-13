from __future__ import annotations

import json
from typing import Any
from chloe.state.db import get_connection


def set(key: str, value: Any) -> None:
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


def get(key: str, default: Any = None) -> Any:
    conn = get_connection()
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    v = row[0]
    if not isinstance(v, str):
        return v  # SQLite returned native numeric type
    return json.loads(v)


def get_all() -> dict[str, Any]:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM kv").fetchall()
    result = {}
    for row in rows:
        v = row[1]
        result[row[0]] = json.loads(v) if isinstance(v, str) else v
    return result


def delete(key: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    conn.commit()

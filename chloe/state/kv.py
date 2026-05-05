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
    return json.loads(row[0])


def get_all() -> dict[str, Any]:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM kv").fetchall()
    return {row[0]: json.loads(row[1]) for row in rows}


def delete(key: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    conn.commit()

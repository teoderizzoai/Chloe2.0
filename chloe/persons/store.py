from __future__ import annotations

from chloe.state.db import get_connection


def get_attachment_depth(person_id: int) -> float:
    conn = get_connection()
    row = conn.execute(
        "SELECT attachment_depth FROM persons WHERE id = ?", (person_id,)
    ).fetchone()
    return row["attachment_depth"] if row else 0.0


def set_attachment_depth(person_id: int, depth: float) -> None:
    depth = max(-1.0, min(1.0, depth))
    conn = get_connection()
    conn.execute(
        "UPDATE persons SET attachment_depth = ? WHERE id = ?",
        (depth, person_id),
    )
    conn.commit()

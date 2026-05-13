from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from chloe.actions.schema import ulid as make_ulid
from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("narrative")

NarrativeKind = Literal["chapter", "event", "revision", "trait_shift", "affect_shift"]


def append_narrative_entry(
    kind: NarrativeKind,
    title: str,
    body: str,
    valence: float | None = None,
    source: str = "system",
    source_ref: str | None = None,
) -> str:
    conn = get_connection()
    entry_id = make_ulid()
    now = datetime.utcnow().isoformat()

    conn.execute(
        """
        INSERT INTO narrative_events (id, kind, title, body, valence, source, source_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_id, kind, title, body, valence, source, source_ref, now),
    )
    conn.commit()
    log.info("narrative_entry_written", kind=kind, title=title, entry_id=entry_id)
    return entry_id


def get_my_story(window_days: int = 30, max_entries: int = 8) -> str:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()

    rows = conn.execute(
        """
        SELECT kind, title, body, valence, created_at
        FROM narrative_events
        WHERE created_at >= ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (cutoff, max_entries),
    ).fetchall()

    if not rows:
        return "Nothing significant recorded in this period."

    lines = []
    for row in rows:
        date_str = row["created_at"][:10]
        lines.append(f"[{date_str}] {row['body']}")

    return "\n".join(lines)


def get_recent_chapter(max_chars: int = 200) -> str:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT body FROM narrative_events
        WHERE kind = 'chapter'
        ORDER BY created_at DESC
        LIMIT 1
        """,
    ).fetchone()

    if not row:
        return ""

    body = row["body"]
    return body[:max_chars] + ("…" if len(body) > max_chars else "")

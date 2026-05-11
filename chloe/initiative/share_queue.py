"""Share queue — things Chloe wants to tell someone.

Items accumulate from curiosity thread outcomes and web search results.
The initiative engine scores them as outreach candidates; timing is
determined by affect + initiative scoring, not triggered immediately.
"""
from __future__ import annotations

from datetime import datetime, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("initiative.share_queue")


def enqueue(
    content: str,
    source: str = "",
    for_person_id: int | None = None,
    urgency: float = 0.1,
) -> int:
    """Add an item to the share queue. Returns the row id."""
    content = content.strip()[:800]
    if not content:
        return -1
    urgency = max(0.0, min(1.0, urgency))
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO share_queue (content, source, for_person, urgency, proposed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (content, source[:200], for_person_id, urgency, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    log.info("share_queued", id=cur.lastrowid, urgency=urgency, source=source[:40])
    return cur.lastrowid


def get_pending(for_person_id: int | None = None, limit: int = 5) -> list[dict]:
    """Return unsent items, highest urgency first."""
    conn = get_connection()
    if for_person_id is not None:
        rows = conn.execute(
            """SELECT * FROM share_queue WHERE shared_at IS NULL AND for_person=?
               ORDER BY urgency DESC, proposed_at ASC LIMIT ?""",
            (for_person_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM share_queue WHERE shared_at IS NULL
               ORDER BY urgency DESC, proposed_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_shared(item_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE share_queue SET shared_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), item_id),
    )
    conn.commit()


def share_queue_candidates(for_person_id: int | None = None) -> list:
    """Return CandidateActions for the top pending share-queue items."""
    from chloe.initiative.candidates import CandidateAction
    items = get_pending(for_person_id=for_person_id, limit=3)
    candidates = []
    for item in items:
        candidates.append(CandidateAction(
            tool="messages",
            verb="send_text",
            args={"body": ""},  # body composed from item content at realize time
            intent=f"Share something I found: {item['content'][:80]}",
            pressure=min(0.75, 0.3 + float(item["urgency"]) * 0.5),
            source="share_queue",
            source_id=str(item["id"]),
        ))
    return candidates

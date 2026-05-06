from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("affect.arc")

_REPAIR_TURNS_NEEDED = 3
_FADE_DAYS = 7


def open_rupture(intensity: float = 0.4, note: str = "") -> int:
    """Open a new rupture arc. Returns the new arc id."""
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO arcs (kind, intensity, state, note, positive_turns_count, started_at)
        VALUES ('rupture', ?, 'active', ?, 0, ?)
        """,
        (intensity, note, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    arc_id = cursor.lastrowid
    log.info("rupture_arc_opened", arc_id=arc_id, intensity=intensity)
    return arc_id


def active_rupture() -> dict | None:
    """Return the most recent active rupture arc, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM arcs WHERE kind = 'rupture' AND state = 'active' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def record_positive_turn(arc_id: int) -> bool:
    """
    Increment positive_turns_count. If >= _REPAIR_TURNS_NEEDED, resolve the arc.
    Returns True if the arc was resolved.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE arcs SET positive_turns_count = positive_turns_count + 1 WHERE id = ?",
        (arc_id,),
    )
    conn.commit()

    row = conn.execute(
        "SELECT positive_turns_count FROM arcs WHERE id = ?", (arc_id,)
    ).fetchone()
    if row and row["positive_turns_count"] >= _REPAIR_TURNS_NEEDED:
        _resolve_arc(arc_id, reason="repair")
        return True
    return False


def fade_stale() -> list[int]:
    """
    Check all active rupture arcs older than _FADE_DAYS days.
    Mark them 'faded' and write an autobiographical memory.
    Returns list of faded arc IDs.
    """
    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_FADE_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT id FROM arcs
        WHERE kind = 'rupture' AND state = 'active' AND started_at < ?
        """,
        (cutoff,),
    ).fetchall()

    faded = []
    for row in rows:
        _fade_arc(row["id"])
        faded.append(row["id"])

    return faded


def should_deliberate_all_kinetic() -> bool:
    """Return True if an active rupture arc exists (all kinetic actions should deliberate)."""
    return active_rupture() is not None


def _resolve_arc(arc_id: int, reason: str = "repair") -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE arcs SET state = 'resolved', active = 0, ended_at = ? WHERE id = ?",
        (now, arc_id),
    )
    conn.commit()

    _write_autobiographical_memory(
        text=f"A period of rupture ended through repair. We found our way back to each other.",
        tags=["rupture", "resolved"],
    )
    log.info("rupture_arc_resolved", arc_id=arc_id, reason=reason)


def _fade_arc(arc_id: int) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE arcs SET state = 'faded', active = 0, ended_at = ? WHERE id = ?",
        (now, arc_id),
    )
    conn.commit()

    _write_autobiographical_memory(
        text="A period of tension faded away without resolution. Some things are left unspoken.",
        tags=["rupture", "faded"],
    )
    log.info("rupture_arc_faded", arc_id=arc_id)


def _write_autobiographical_memory(text: str, tags: list[str]) -> None:
    try:
        from chloe.memory.store import add
        add(
            kind="autobiographical",
            text=text,
            source="arc",
            tags=tags,
            confidence=0.5,
            salience=0.7,
        )
    except Exception as exc:
        log.warning("arc_memory_write_failed", error=str(exc))

"""Emotional residue — slow-moving valence/arousal bias from recent affect events.

Reads `affect_records` from the last hour, computes a recency-weighted average
of valence and arousal deltas. The result is a small persistent nudge applied
to `affect_state` each tick.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("inner.residue")

WINDOW_MINUTES = 60
DECAY_MINUTES = 30  # weight halves every 30 minutes inside the window


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def compute_residue(now: datetime | None = None) -> dict:
    """Return {'valence': float, 'arousal': float, 'count': int}.

    Values are bounded to [-0.5, 0.5] (valence) and [0, 0.5] (arousal) so they
    can be added to base affect without overwhelming it.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff = (now - timedelta(minutes=WINDOW_MINUTES)).isoformat()
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT valence_delta, arousal_delta, intensity, created_at
        FROM affect_records
        WHERE created_at >= ?
        ORDER BY created_at DESC
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        return {"valence": 0.0, "arousal": 0.0, "count": 0}

    val_sum = 0.0
    aro_sum = 0.0
    weight_sum = 0.0

    for row in rows:
        ts = _parse(row["created_at"])
        if ts is None:
            continue
        age_min = max(0.0, (now - ts).total_seconds() / 60.0)
        w = math.pow(0.5, age_min / DECAY_MINUTES) * max(0.05, row["intensity"] or 0.0)
        val_sum += (row["valence_delta"] or 0.0) * w
        aro_sum += (row["arousal_delta"] or 0.0) * w
        weight_sum += w

    if weight_sum == 0.0:
        return {"valence": 0.0, "arousal": 0.0, "count": len(rows)}

    valence = max(-0.5, min(0.5, val_sum / weight_sum))
    arousal = max(0.0, min(0.5, aro_sum / weight_sum))

    return {"valence": valence, "arousal": arousal, "count": len(rows)}


def record_event(intensity: float, valence_delta: float, arousal_delta: float, trigger: str) -> int:
    """Append an affect_records row. Returns the new row id."""
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO affect_records (intensity, valence_delta, arousal_delta, trigger, residue, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            max(0.0, min(1.0, intensity)),
            max(-1.0, min(1.0, valence_delta)),
            max(-1.0, min(1.0, arousal_delta)),
            trigger,
            0.0,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    log.info("affect_event", id=cur.lastrowid, trigger=trigger, intensity=round(intensity, 3))
    return cur.lastrowid

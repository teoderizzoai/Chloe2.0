"""Interest Garden — slow-growing list of things Chloe cares about.

PRD §13.3: ≤8 active interests. Daily decay −0.02. +0.1 on relevant action.
Nightly job archives interests with intensity < 0.05.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("identity.interest_garden")

DAILY_DECAY = 0.02
ACTION_BOOST = 0.10
ARCHIVE_THRESHOLD = 0.05
MAX_ACTIVE = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fuzzy_match(label: str, conn) -> int | None:
    """Return existing interest id if label overlaps significantly with an active one."""
    label_l = label.lower().strip()
    rows = conn.execute(
        "SELECT id, label FROM interest_garden WHERE intensity > 0"
    ).fetchall()
    for row in rows:
        existing_l = (row["label"] or "").lower().strip()
        # Exact or substring match (either direction, min 8 chars to avoid noise)
        if len(label_l) >= 8 and (label_l in existing_l or existing_l in label_l):
            return row["id"]
        # Word-overlap: ≥50% of words in the shorter label appear in the longer
        words_new = set(label_l.split())
        words_ex = set(existing_l.split())
        shorter = words_new if len(words_new) <= len(words_ex) else words_ex
        longer = words_ex if len(words_new) <= len(words_ex) else words_new
        if len(shorter) >= 2 and len(shorter & longer) / len(shorter) >= 0.5:
            return row["id"]
    return None


def add_interest(label: str, why: str = "", intensity: float = 0.3, category: str = "curiosity") -> int:
    label = label.strip()[:50]  # enforce short labels even if schema truncation didn't fire
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM interest_garden WHERE label=? COLLATE NOCASE", (label,)
    ).fetchone()
    if existing:
        boost_interest(label, ACTION_BOOST, why=why)
        return existing["id"]

    # Fuzzy dedup — boost the closest existing interest instead of fragmenting
    fuzzy_id = _fuzzy_match(label, conn)
    if fuzzy_id is not None:
        conn.execute(
            "UPDATE interest_garden SET intensity=MIN(1.0, intensity+?), last_engaged_at=? WHERE id=?",
            (ACTION_BOOST, _now(), fuzzy_id),
        )
        conn.commit()
        log.info("interest_fuzzy_boosted", id=fuzzy_id, new_label=label)
        return fuzzy_id

    active = conn.execute(
        "SELECT COUNT(*) AS n FROM interest_garden WHERE intensity > ?", (ARCHIVE_THRESHOLD,)
    ).fetchone()
    if active and active["n"] >= MAX_ACTIVE:
        # Bump out the weakest active interest to make room.
        weakest = conn.execute(
            "SELECT id, label FROM interest_garden ORDER BY intensity ASC LIMIT 1"
        ).fetchone()
        if weakest:
            conn.execute("UPDATE interest_garden SET intensity=0 WHERE id=?", (weakest["id"],))
            log.info("interest_evicted", id=weakest["id"], label=weakest["label"])

    intensity = max(0.0, min(1.0, intensity))
    # interest_garden has no `category` column — encode via the why field.
    why_with_cat = f"[{category}] {why}".strip()
    cur = conn.execute(
        """INSERT INTO interest_garden (label, why, intensity, created_at)
           VALUES (?, ?, ?, ?)""",
        (label, why_with_cat, intensity, _now()),
    )
    conn.commit()
    log.info("interest_added", id=cur.lastrowid, label=label, intensity=intensity)
    return cur.lastrowid


def boost_interest(label: str, delta: float = ACTION_BOOST, why: str | None = None) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, intensity FROM interest_garden WHERE label=? COLLATE NOCASE",
        (label,),
    ).fetchone()
    if not row:
        return False
    new_int = max(0.0, min(1.0, row["intensity"] + delta))
    conn.execute(
        "UPDATE interest_garden SET intensity=?, last_engaged_at=? WHERE id=?",
        (new_int, _now(), row["id"]),
    )
    conn.commit()
    log.info("interest_boosted", id=row["id"], label=label, delta=delta, new_intensity=new_int)
    return True


def decay_interests(rate: float = DAILY_DECAY) -> int:
    """Apply linear decay to all interests. Returns count touched."""
    conn = get_connection()
    cur = conn.execute(
        "UPDATE interest_garden SET intensity = MAX(0, intensity - ?) WHERE intensity > 0",
        (rate,),
    )
    conn.commit()
    log.info("interest_decay", rate=rate, rows=cur.rowcount)
    return cur.rowcount


def archive_low_interests(threshold: float = ARCHIVE_THRESHOLD) -> int:
    """Drop interests below the threshold to intensity=0 (effectively archived).

    The schema has no `archived` column for interest_garden, so we mark them
    as zero-intensity and write an autobiographical memory of the abandonment.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, label, why FROM interest_garden WHERE intensity > 0 AND intensity < ?",
        (threshold,),
    ).fetchall()
    if not rows:
        return 0

    from chloe.memory.store import add as memory_add
    for row in rows:
        try:
            memory_add(
                kind="autobiographical",
                text=f"Let go of an interest in {row['label']}. It faded.",
                source="interest_archive",
                source_ref=str(row["id"]),
                tags=["interest", "archived"],
                weight=0.4,
            )
        except Exception as exc:
            log.warning("interest_archive_memory_failed", error=str(exc))

    placeholders = ",".join("?" * len(rows))
    conn.execute(
        f"UPDATE interest_garden SET intensity=0 WHERE id IN ({placeholders})",
        [r["id"] for r in rows],
    )
    conn.commit()
    log.info("interests_archived", count=len(rows))
    return len(rows)


def top_interests(n: int = 3) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, label, why, intensity, last_engaged_at FROM interest_garden "
        "WHERE intensity > 0 ORDER BY intensity DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]

from __future__ import annotations

import json
from datetime import datetime

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("belief_revision")

REVISION_CONFIDENCE_DELTA = 0.25
OVERLAP_TAG_THRESHOLD     = 2


def upsert_belief_with_revision(
    content: str,
    confidence: float,
    source: str,
    tags: list[str],
    revision_note: str | None = None,
) -> int:
    """
    Insert a new belief. If a semantically overlapping belief exists and
    confidence differs enough, archive the old one and link them.
    Returns the new belief's rowid.
    """
    conn = get_connection()
    now = datetime.utcnow().isoformat()

    existing = _find_overlapping_belief(tags, conn)

    supersedes = None
    if existing and _is_contradiction(existing["confidence"], confidence):
        old_id = existing["id"]
        # We need the new id first — insert then update superseded_by
        cursor = conn.execute(
            """
            INSERT INTO inner_beliefs (text, confidence, source, tags, archived, updated_at, created_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (content, confidence, source, json.dumps(tags), now, now),
        )
        new_id = cursor.lastrowid

        conn.execute(
            "UPDATE inner_beliefs SET archived=1, superseded_by=?, updated_at=? WHERE id=?",
            (str(new_id), now, old_id),
        )
        conn.execute(
            "UPDATE inner_beliefs SET supersedes=? WHERE id=?",
            (str(old_id), new_id),
        )

        if revision_note:
            conn.execute(
                "UPDATE inner_beliefs SET revision_note=? WHERE id=?",
                (revision_note, new_id),
            )

        _write_revision_memory(existing["text"], content, revision_note or f"Updated after new information from {source}.")
        log.info("belief_revised", old_id=old_id, new_id=new_id, source=source)

        conn.commit()
        return new_id
    else:
        cursor = conn.execute(
            """
            INSERT INTO inner_beliefs (text, confidence, source, tags, archived, updated_at, created_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (content, confidence, source, json.dumps(tags), now, now),
        )
        new_id = cursor.lastrowid
        conn.commit()
        return new_id


def _find_overlapping_belief(tags: list[str], conn) -> dict | None:
    if not tags:
        return None

    rows = conn.execute(
        "SELECT id, text, confidence, tags FROM inner_beliefs WHERE archived = 0 ORDER BY confidence DESC"
    ).fetchall()

    for row in rows:
        try:
            existing_tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"] or []
        except Exception:
            existing_tags = []

        if len(set(tags) & set(existing_tags)) >= OVERLAP_TAG_THRESHOLD:
            return dict(row)

    return None


def _is_contradiction(old_confidence: float, new_confidence: float) -> bool:
    return abs(new_confidence - old_confidence) >= REVISION_CONFIDENCE_DELTA


def _write_revision_memory(old_content: str, new_content: str, note: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, weight, tags, created_at)
        VALUES ('autobiographical', ?, 'belief_revision', 0.7,
                '["belief_revision","autobiographical"]', datetime('now'))
        """,
        (
            f"I updated my understanding: I used to believe '{old_content[:80]}' "
            f"but now believe '{new_content[:80]}'. {note}",
        ),
    )
    conn.commit()
    log.info("revision_memory_written")

    # Write narrative entry if available
    try:
        from chloe.identity.narrative import append_narrative_entry
        append_narrative_entry(
            kind="revision",
            title=f"Updated belief: {new_content[:40]}",
            body=f"I used to believe '{old_content[:60]}' but updated that to '{new_content[:60]}'.",
            valence=None,
            source="belief_revision",
        )
    except Exception:
        pass


def get_belief_confidence_summary(tags: list[str]) -> dict | None:
    conn = get_connection()

    rows = conn.execute(
        "SELECT text, confidence, tags FROM inner_beliefs WHERE archived = 0"
    ).fetchall()

    matches = []
    for row in rows:
        try:
            btags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"] or []
        except Exception:
            btags = []
        if len(set(tags) & set(btags)) >= 1:
            matches.append({"text": row["text"], "confidence": row["confidence"]})

    if not matches:
        return None

    avg_conf = sum(m["confidence"] for m in matches) / len(matches)
    return {
        "count": len(matches),
        "avg_confidence": round(avg_conf, 2),
        "uncertain": avg_conf < 0.5,
        "top_belief": max(matches, key=lambda m: m["confidence"])["text"],
    }

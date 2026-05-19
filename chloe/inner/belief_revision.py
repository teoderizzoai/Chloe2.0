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


# ---------------------------------------------------------------------------
# World beliefs (Block 1/3) — confidence floor + consistency check + tensions
# ---------------------------------------------------------------------------

WORLD_BELIEF_FLOOR = 0.2
LOOSELY_HELD_THRESHOLD = 0.5
CONFIRMATION_BUMP = 0.08


async def store_new_belief(
    topic: str,
    belief: str,
    proposed_confidence: float = 0.5,
    noticing: bool = False,
) -> int | None:
    """Persist a new world belief with the developmental floor + consistency check.

    On first storage, confidence is clamped to WORLD_BELIEF_FLOOR (0.2). Confidence
    rises only via repeated confirmation across reflect windows — each repeat call
    on the same topic bumps confidence by CONFIRMATION_BUMP.

    Before persisting, runs a Flash consistency call against existing beliefs.
    If a tension surfaces, the new belief is stored anyway, points at the
    conflicting belief via `contradicts`, and an inner_tension is opened so
    Chloe can sit with the contradiction rather than auto-overwrite.
    """
    conn = get_connection()
    topic = topic.strip()[:80]
    belief = belief.strip()
    if not topic or not belief:
        return None

    existing_same = conn.execute(
        "SELECT id, confidence, confirmation_count FROM world_beliefs WHERE topic=?", (topic,)
    ).fetchone()

    # Async LLM consistency check against all existing beliefs except the same topic.
    contradicts_id: int | None = None
    try:
        contradicts_id = await _check_consistency_async(topic, belief)
    except Exception as exc:
        log.warning("belief_consistency_check_failed", error=str(exc))

    if existing_same:
        new_count = (existing_same["confirmation_count"] or 1) + 1
        # Confirmation lifts confidence by a small step, bounded below by floor.
        new_conf = min(0.95, max(WORLD_BELIEF_FLOOR, existing_same["confidence"] + CONFIRMATION_BUMP))
        held_loosely = new_conf < LOOSELY_HELD_THRESHOLD
        conn.execute(
            """UPDATE world_beliefs
               SET belief=?, confidence=?, held_loosely=?, noticing=?,
                   confirmation_count=?, contradicts=COALESCE(?, contradicts),
                   updated_at=datetime('now')
               WHERE id=?""",
            (belief, new_conf, 1 if held_loosely else 0, 1 if noticing else 0,
             new_count, contradicts_id, existing_same["id"]),
        )
        conn.commit()
        log.info("belief_confirmed", id=existing_same["id"], topic=topic,
                 confidence=round(new_conf, 2), confirmation=new_count)
        if contradicts_id:
            _open_belief_tension(existing_same["id"], contradicts_id, topic)
        return existing_same["id"]

    # New topic — clamp to confidence floor regardless of LLM proposal.
    initial_conf = max(WORLD_BELIEF_FLOOR, min(0.5, float(proposed_confidence)))
    held_loosely = initial_conf < LOOSELY_HELD_THRESHOLD
    cursor = conn.execute(
        """INSERT INTO world_beliefs
             (topic, belief, confidence, source, held_loosely, noticing, contradicts, confirmation_count)
           VALUES (?, ?, ?, 'reflect', ?, ?, ?, 1)""",
        (topic, belief, initial_conf, 1 if held_loosely else 0, 1 if noticing else 0, contradicts_id),
    )
    new_id = cursor.lastrowid
    conn.commit()
    log.info("belief_new", id=new_id, topic=topic, confidence=round(initial_conf, 2),
             noticing=noticing, contradicts=contradicts_id)
    if contradicts_id:
        _open_belief_tension(new_id, contradicts_id, topic)
    return new_id


async def _check_consistency_async(topic: str, belief: str) -> int | None:
    """Flash call to detect semantic contradictions between the new belief and existing ones.

    The LLM is better than the old lexical heuristic at catching inverses like
    "people are more honest than they seem" vs "people hide their real feelings."
    Falls back to the lexical heuristic if no API key is available or the call
    fails, so the system degrades gracefully in offline / test contexts.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, topic, belief FROM world_beliefs WHERE topic != ? ORDER BY confidence DESC LIMIT 20",
        (topic,),
    ).fetchall()
    if not rows:
        return None

    try:
        from chloe.llm.gemini import GeminiClient
        from chloe.llm.schemas import BeliefConsistencyResult

        existing_lines = "\n".join(f"- id={r['id']} | {r['topic']}: {r['belief']}" for r in rows)
        client = GeminiClient()
        result = await client.flash(
            "belief_consistency.md",
            {"new_topic": topic, "new_belief": belief, "existing_beliefs": existing_lines},
            BeliefConsistencyResult,
        )
        if result is None:
            return _check_consistency_lexical(topic, belief, rows)

        cid = result.get("contradicts_id") if isinstance(result, dict) else getattr(result, "contradicts_id", None)
        if cid is not None:
            log.info("belief_llm_tension_detected", new_topic=topic, conflict_with=cid)
            return int(cid)
        return None
    except Exception as exc:
        log.warning("belief_llm_consistency_failed", error=str(exc))
        return _check_consistency_lexical(topic, belief, rows)


def _check_consistency_lexical(topic: str, belief: str, rows) -> int | None:
    """Fallback lexical contradiction heuristic (word overlap + negation polarity)."""
    negators = {"not", "never", "no", "isn't", "doesn't", "won't", "can't"}
    new_has_neg = any(w in negators for w in belief.lower().split())
    new_words = set(belief.lower().split())
    for r in rows:
        existing_words = set(r["belief"].lower().split())
        overlap = new_words & existing_words
        if len(overlap) >= 4:
            existing_has_neg = any(w in negators for w in r["belief"].lower().split())
            if new_has_neg != existing_has_neg:
                log.info("belief_lexical_tension_detected", new_topic=topic, conflict_with=r["id"])
                return int(r["id"])
    return None


def _check_consistency_sync(topic: str, belief: str) -> int | None:
    """Synchronous wrapper — tries to run the async LLM check in the running loop,
    falls back to the lexical heuristic when no loop is available.
    """
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        # Schedule as a task so we don't block the event loop; result is discarded
        # here (async path in store_new_belief should call _check_consistency_async
        # directly). This sync wrapper exists only for backwards compatibility.
        loop.create_task(_check_consistency_async_and_log(topic, belief))
        # Return None now — the tension will be opened asynchronously if found.
        return None
    except RuntimeError:
        # No running loop — use the lexical fallback synchronously.
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, topic, belief FROM world_beliefs WHERE topic != ? ORDER BY confidence DESC LIMIT 20",
            (topic,),
        ).fetchall()
        return _check_consistency_lexical(topic, belief, rows)


async def _check_consistency_async_and_log(topic: str, belief: str) -> None:
    """Helper used by the sync wrapper — runs the async check and opens a tension if one is found."""
    try:
        cid = await _check_consistency_async(topic, belief)
        if cid:
            conn = get_connection()
            existing_same = conn.execute(
                "SELECT id FROM world_beliefs WHERE topic=?", (topic,)
            ).fetchone()
            if existing_same:
                _open_belief_tension(existing_same["id"], cid, topic)
    except Exception as exc:
        log.warning("belief_async_consistency_log_failed", error=str(exc))


def _open_belief_tension(new_belief_id: int, conflict_id: int, topic: str) -> None:
    """Create an inner_tension reflecting an unresolved belief conflict.

    When neither belief has high enough confidence to dominate, marks both as
    ambivalent rather than forcing adjudication — holding the contradiction
    without resolving it.
    """
    conn = get_connection()
    try:
        new_row = conn.execute(
            "SELECT confidence FROM world_beliefs WHERE id=?", (new_belief_id,)
        ).fetchone()
        old_row = conn.execute(
            "SELECT confidence FROM world_beliefs WHERE id=?", (conflict_id,)
        ).fetchone()

        if new_row and old_row:
            new_conf = float(new_row["confidence"] or 0.0)
            old_conf = float(old_row["confidence"] or 0.0)
            if new_conf < 0.55 and old_conf < 0.55:
                conn.execute(
                    "UPDATE world_beliefs SET ambivalent=1, ambivalent_with=? WHERE id=?",
                    (conflict_id, new_belief_id),
                )
                conn.execute(
                    "UPDATE world_beliefs SET ambivalent=1, ambivalent_with=? WHERE id=?",
                    (new_belief_id, conflict_id),
                )
                conn.commit()
                log.info("belief_ambivalent_pair_marked", a=new_belief_id, b=conflict_id, topic=topic)
                return  # Ambivalence, not a tension to resolve
    except Exception as exc:
        log.warning("ambivalent_marking_failed", error=str(exc))

    from chloe.inner.pressure import add_tension
    try:
        add_tension(
            f"Two views I hold sit in tension around '{topic}'.",
            tags=["unresolved", "belief_conflict"],
            pressure=0.55,
        )
    except Exception as exc:
        log.warning("belief_tension_record_failed", error=str(exc))


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

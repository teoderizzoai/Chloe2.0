from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from chloe.state.db import get_connection
from chloe.memory.retrieval import Memory, add_to_chroma
from chloe.observability.logging import get_logger
from chloe.observability.metrics import chloe_memory_writes_total

log = get_logger("memory.store")

HALF_LIVES: dict[str, float] = {
    "episodic": 60.0,
    "semantic": 180.0,
    "autobiographical": 365.0,
    "procedural": 90.0,
}


def add(
    kind: str,
    text: str,
    source: str | None = None,
    source_ref: str | None = None,
    tags: list | None = None,
    artifact_refs: list | None = None,
    weight: float = 1.0,
    salience: float = 0.5,
    confidence: float = 1.0,
    archived_tier: str = "hot",
    collection_name: str = "memories_v2",
    unprocessed: bool = False,
    emotional_valence: float | None = None,
    emotional_arousal: float | None = None,
    confidential_to: int | None = None,
) -> int:
    """Insert into SQLite and ChromaDB. Returns the new memory id.

    When `unprocessed=True`, the memory is marked as a not-yet-made-sense-of
    experience: it surfaces in chat context with phrasing like "haven't fully
    worked this out yet" and is excluded from belief / trait formation. The
    weekly review pass decides whether to promote or keep it unprocessed.

    `emotional_valence` and `emotional_arousal` enable mood-congruent retrieval
    in query_fast — memories whose register aligns with Chloe's current affect
    score higher. Set these whenever the emotional tone of the content is known.

    `confidential_to` gates display in chat: a memory with confidential_to=pid
    is annotated "(told in confidence)" when retrieved for other persons.
    """
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO memories
          (kind, text, source, source_ref, tags, artifact_refs, weight, salience, confidence,
           archived_tier, unprocessed, emotional_valence, emotional_arousal, confidential_to,
           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            text,
            source,
            source_ref,
            json.dumps(tags or []),
            json.dumps(artifact_refs or []),
            weight,
            salience,
            confidence,
            archived_tier,
            1 if unprocessed else 0,
            emotional_valence,
            emotional_arousal,
            confidential_to,
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    memory_id = cursor.lastrowid
    chloe_memory_writes_total.labels(kind=kind).inc()
    add_to_chroma(
        memory_id=memory_id,
        text=text,
        kind=kind,
        source=source,
        artifact_refs=artifact_refs or [],
        collection_name=collection_name,
        emotional_valence=emotional_valence,
        emotional_arousal=emotional_arousal,
    )
    return memory_id


def update_memory_affect(
    memory_id: int,
    emotional_valence: float,
    emotional_arousal: float,
    collection_name: str = "memories_v2",
) -> None:
    """Update emotional valence/arousal on an existing memory and re-upsert to Chroma.

    Called from the background extraction pass after person_valence/arousal are
    known — the chat memory is written first, then enriched with affect metadata.
    """
    conn = get_connection()
    row = conn.execute("SELECT text, kind, source FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not row:
        return
    conn.execute(
        "UPDATE memories SET emotional_valence=?, emotional_arousal=?, updated_at=? WHERE id=?",
        (emotional_valence, emotional_arousal, datetime.now(timezone.utc).isoformat(), memory_id),
    )
    conn.commit()
    add_to_chroma(
        memory_id=memory_id,
        text=row["text"],
        kind=row["kind"],
        source=row["source"],
        artifact_refs=[],
        collection_name=collection_name,
        emotional_valence=emotional_valence,
        emotional_arousal=emotional_arousal,
    )


def mark_unprocessed(memory_id: int, value: bool = True) -> None:
    """Toggle the unprocessed flag on an existing memory."""
    conn = get_connection()
    conn.execute(
        "UPDATE memories SET unprocessed = ? WHERE id = ?",
        (1 if value else 0, memory_id),
    )
    conn.commit()


def consider_unprocessed(salience: float, ambiguity: float) -> bool:
    """Decide whether a memory should be filed as unprocessed.

    Rule (per Chloe 3.0 Block 1, Step 3): ambiguity > 0.6 AND salience > 0.4.
    Centralized so any extraction pipeline can apply the same threshold.
    """
    return ambiguity > 0.6 and salience > 0.4


async def grade(
    candidates: list[Memory],
    message: str,
    history: list[str],
    affect,
    keep: int = 5,
    collection_name: str = "memories_v2",
) -> list[Memory]:
    """
    Use Gemini Flash to select the top-K most relevant memories from candidates.
    Returns up to `keep` Memory objects in relevance order.
    """
    if not candidates:
        return []
    if len(candidates) <= keep:
        return candidates

    from chloe.llm.gemini import get_client
    from chloe.llm.schemas import GradeResult

    candidates_text = _format_candidates(candidates)
    affect_label = _affect_hint(affect)
    history_text = "\n".join(history[-3:]) if history else ""

    llm = get_client()
    result = await llm.flash(
        "grade_memories.md",
        {
            "message": message,
            "history": history_text,
            "affect_label": affect_label,
            "candidates_text": candidates_text,
            "keep": keep,
        },
        schema=GradeResult,
    )

    if not result:
        log.warning("grade_llm_failed", candidates=len(candidates))
        return candidates[:keep]

    selected_ids: list[int] = []
    relevance_notes: dict[int, str] = {}

    raw_selected = result.get("selected", []) if isinstance(result, dict) else []
    for item in raw_selected:
        if isinstance(item, dict):
            mem_id = item.get("id")
            note = item.get("relevance_note", "")
        else:
            mem_id = getattr(item, "id", None)
            note = getattr(item, "relevance_note", "")
        if mem_id is not None:
            selected_ids.append(int(mem_id))
            relevance_notes[int(mem_id)] = note

    id_to_mem = {m.id: m for m in candidates}
    graded: list[Memory] = []
    for mid in selected_ids:
        if mid in id_to_mem:
            mem = id_to_mem[mid]
            mem.relevance_note = relevance_notes.get(mid, "")
            graded.append(mem)

    return graded[:keep]


def decay_all() -> int:
    """
    Apply exponential half-life decay to all hot-tier memories.
    Returns the number of memories updated.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc)

    rows = conn.execute(
        "SELECT id, kind, weight, created_at FROM memories WHERE archived_tier = 'hot'"
    ).fetchall()

    updated = 0
    for row in rows:
        kind = row["kind"]
        old_weight = row["weight"]
        created = row["created_at"]

        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            continue

        age_days = (now - created_dt).total_seconds() / 86400.0
        if age_days <= 0:
            continue

        new_weight = decay(old_weight, age_days, kind)
        if abs(new_weight - old_weight) < 1e-6:
            continue

        conn.execute(
            "UPDATE memories SET weight = ?, updated_at = ? WHERE id = ?",
            (new_weight, now.isoformat(), row["id"]),
        )
        updated += 1

    conn.commit()
    log.info("memory_decay_applied", updated=updated)
    return updated


def decay(weight: float, age_days: float, kind: str) -> float:
    """Exponential half-life decay: w * 0.5^(age_days / half_life)."""
    half_life = HALF_LIVES.get(kind, 60.0)
    return weight * math.pow(0.5, age_days / half_life)


def _format_candidates(candidates: list[Memory]) -> str:
    lines = []
    for m in candidates:
        tags_str = ", ".join(m.tags) if m.tags else ""
        artifact_hint = " [anchored]" if m.artifact_refs else ""
        lines.append(f"ID {m.id} ({m.kind}{artifact_hint}): {m.text[:200]}"
                     + (f" [tags: {tags_str}]" if tags_str else ""))
    return "\n".join(lines)


def _affect_hint(affect) -> str:
    if affect is None:
        return "steady"
    label = getattr(affect, "_cached_label", None)
    if label:
        return label
    v = getattr(affect, "valence", 0.0)
    a = getattr(affect, "arousal", 0.4)
    if v > 0.3:
        return "warm and open"
    if v < -0.3:
        return "subdued and inward"
    if a > 0.7:
        return "energized"
    return "steady"


def delete_from_chroma(memory_id: int, collection_name: str = "memories_v2") -> None:
    """Remove a memory's embedding from Chroma."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(collection_name)
        collection.delete(ids=[str(memory_id)])
    except Exception as exc:
        log.warning("chroma_delete_failed", memory_id=memory_id, error=str(exc))


def chroma_add(memory_id: int, text: str, kind: str = "semantic",
               collection_name: str = "memories_v2") -> None:
    """Add or update a memory's embedding in Chroma."""
    from chloe.memory.retrieval import add_to_chroma
    add_to_chroma(memory_id=memory_id, text=text, kind=kind,
                  source="cluster_summary", artifact_refs=[],
                  collection_name=collection_name)


def chroma_count(collection_name: str = "memories_v2") -> int:
    """Return total number of embeddings in Chroma collection."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(collection_name)
        return collection.count()
    except Exception as exc:
        log.warning("chroma_count_failed", error=str(exc))
        return 0

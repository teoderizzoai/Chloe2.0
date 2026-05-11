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


_INTEREST_EMBED_COLLECTION = "interest_labels_v1"
_SEMANTIC_DEDUP_THRESHOLD = 0.85  # cosine similarity above this → same concept


def _semantic_match_interest(label: str, conn) -> int | None:
    """Embed the new label and compare against existing interest embeddings.

    Falls back to the cheaper fuzzy heuristic when Chroma is unavailable.
    Returns the id of the matching existing interest, or None.
    """
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(_INTEREST_EMBED_COLLECTION)
        if collection.count() == 0:
            return None
        result = collection.query(
            query_texts=[label],
            n_results=1,
            include=["distances", "metadatas"],
        )
        distances = result.get("distances", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        if not distances:
            return None
        similarity = 1.0 / (1.0 + distances[0])
        if similarity >= _SEMANTIC_DEDUP_THRESHOLD and metas:
            interest_id = metas[0].get("interest_id")
            if interest_id is not None:
                row = conn.execute(
                    "SELECT id FROM interest_garden WHERE id=? AND intensity > 0",
                    (int(interest_id),),
                ).fetchone()
                if row:
                    log.info(
                        "interest_semantic_dedup",
                        new_label=label,
                        existing_id=row["id"],
                        similarity=round(similarity, 3),
                    )
                    return row["id"]
    except Exception as exc:
        log.debug("interest_semantic_match_failed", error=str(exc))
    return None


def _register_interest_embedding(interest_id: int, label: str) -> None:
    """Add a new interest label to the embedding collection for future dedup."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(_INTEREST_EMBED_COLLECTION)
        collection.upsert(
            ids=[str(interest_id)],
            documents=[label],
            metadatas=[{"interest_id": interest_id}],
        )
    except Exception as exc:
        log.debug("interest_embedding_register_failed", error=str(exc))


def _fuzzy_match(label: str, conn) -> int | None:
    """Lexical fuzzy fallback: return existing interest id if label overlaps significantly."""
    label_l = label.lower().strip()
    rows = conn.execute(
        "SELECT id, label FROM interest_garden WHERE intensity > 0"
    ).fetchall()
    for row in rows:
        existing_l = (row["label"] or "").lower().strip()
        if len(label_l) >= 8 and (label_l in existing_l or existing_l in label_l):
            return row["id"]
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

    # Semantic dedup (embedding similarity) — try first, fall back to fuzzy
    sem_id = _semantic_match_interest(label, conn)
    if sem_id is not None:
        conn.execute(
            "UPDATE interest_garden SET intensity=MIN(1.0, intensity+?), last_engaged_at=? WHERE id=?",
            (ACTION_BOOST, _now(), sem_id),
        )
        conn.commit()
        log.info("interest_semantic_boosted", id=sem_id, new_label=label)
        return sem_id

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
    # New interests always start at gen_level 0 (a specific instance she encountered).
    # Promotion to 1/2/3 only happens via the Narrative Weaver based on accumulated evidence.
    cur = conn.execute(
        """INSERT INTO interest_garden (label, why, intensity, gen_level, created_at)
           VALUES (?, ?, ?, 0, ?)""",
        (label, why_with_cat, intensity, _now()),
    )
    conn.commit()
    new_id = cur.lastrowid
    log.info("interest_added", id=new_id, label=label, intensity=intensity, gen_level=0)
    _register_interest_embedding(new_id, label)
    return new_id


def promote_interest(interest_id: int, new_level: int, evidence: str = "") -> bool:
    """Promote an interest one level up (0→1→2→3). Only the Narrative Weaver should call this.

    No skipping levels: must increment by exactly 1. Returns True on success.
    """
    if new_level not in (1, 2, 3):
        return False
    conn = get_connection()
    row = conn.execute(
        "SELECT gen_level, label FROM interest_garden WHERE id=?", (interest_id,)
    ).fetchone()
    if not row:
        return False
    if new_level != row["gen_level"] + 1:
        log.warning(
            "interest_promote_skip_rejected",
            id=interest_id, current=row["gen_level"], requested=new_level,
        )
        return False
    conn.execute(
        "UPDATE interest_garden SET gen_level=? WHERE id=?",
        (new_level, interest_id),
    )
    conn.commit()
    log.info(
        "interest_promoted",
        id=interest_id, label=row["label"], from_level=row["gen_level"], to_level=new_level,
        evidence=evidence[:120],
    )
    return True


CURIOSITY_QUESTION_THRESHOLD = 0.7
_KV_QUESTION_PREFIX = "interest:curiosity_question:"


def boost_interest(label: str, delta: float = ACTION_BOOST, why: str | None = None) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, intensity, why, gen_level FROM interest_garden WHERE label=? COLLATE NOCASE",
        (label,),
    ).fetchone()
    if not row:
        return False
    old_int = row["intensity"]
    new_int = max(0.0, min(1.0, old_int + delta))
    conn.execute(
        "UPDATE interest_garden SET intensity=?, last_engaged_at=? WHERE id=?",
        (new_int, _now(), row["id"]),
    )
    conn.commit()
    log.info("interest_boosted", id=row["id"], label=label, delta=delta, new_intensity=new_int)

    # Fire curiosity question generation when intensity first crosses the threshold
    if old_int < CURIOSITY_QUESTION_THRESHOLD <= new_int:
        _schedule_curiosity_question(
            interest_id=row["id"],
            label=label,
            why=row["why"] or "",
            gen_level=int(row["gen_level"] or 0),
        )

    return True


def _schedule_curiosity_question(interest_id: int, label: str, why: str, gen_level: int) -> None:
    """Schedule async Flash call to generate curiosity question; cache in kv."""
    from chloe.state.kv import get as kv_get
    if kv_get(f"{_KV_QUESTION_PREFIX}{interest_id}"):
        return  # already generated
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(
            _generate_and_cache_curiosity_question(interest_id, label, why, gen_level)
        )
    except RuntimeError:
        # No running event loop — mark as pending for next async context
        from chloe.state.kv import set as kv_set
        kv_set(f"interest:question_pending:{interest_id}", True)
    log.info("curiosity_question_scheduled", interest_id=interest_id, label=label)


async def _generate_and_cache_curiosity_question(
    interest_id: int,
    label: str,
    why: str,
    gen_level: int,
) -> None:
    """Flash call: generate a curiosity question and store in kv."""
    try:
        from chloe.llm.gemini import GeminiClient
        from chloe.state.kv import set as kv_set
        from pydantic import BaseModel, Field

        class CuriosityQuestion(BaseModel):
            question: str = Field(max_length=200)

        conn = get_connection()
        recent_rows = conn.execute(
            "SELECT text FROM memories WHERE source='chat' ORDER BY id DESC LIMIT 5"
        ).fetchall()
        recent_evidence = "\n".join(f"- {r['text'][:150]}" for r in recent_rows) or "(none yet)"

        client = GeminiClient()
        result = await client.flash(
            "generate_curiosity_question.md",
            {"label": label, "why": why, "gen_level": gen_level, "recent_evidence": recent_evidence},
            CuriosityQuestion,
        )
        if result:
            question = (result.get("question") if isinstance(result, dict) else getattr(result, "question", "")) or ""
            if question:
                kv_set(f"{_KV_QUESTION_PREFIX}{interest_id}", question.strip())
                log.info("curiosity_question_cached", interest_id=interest_id, question=question[:80])
    except Exception as exc:
        log.warning("curiosity_question_generation_failed", interest_id=interest_id, error=str(exc))


async def drain_pending_curiosity_questions() -> int:
    """Generate curiosity questions for interests flagged as pending.

    boost_interest() sets interest:question_pending:{id} when called outside an
    async context (no running event loop). This drain processes that backlog and
    should be called once per tick or per reflect cycle.
    """
    from chloe.state.kv import get as kv_get, set as kv_set, delete as kv_delete
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, label, why, gen_level FROM interest_garden WHERE intensity > 0"
    ).fetchall()

    drained = 0
    for row in rows:
        pending_key = f"interest:question_pending:{row['id']}"
        if not kv_get(pending_key):
            continue
        if kv_get(f"{_KV_QUESTION_PREFIX}{row['id']}"):
            kv_delete(pending_key)
            continue
        await _generate_and_cache_curiosity_question(
            interest_id=row["id"],
            label=row["label"],
            why=row["why"] or "",
            gen_level=int(row["gen_level"] or 0),
        )
        kv_delete(pending_key)
        drained += 1

    if drained:
        log.info("curiosity_questions_drained", count=drained)
    return drained


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
        "SELECT id, label, why, intensity, gen_level, last_engaged_at FROM interest_garden "
        "WHERE intensity > 0 ORDER BY intensity DESC LIMIT ?",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]

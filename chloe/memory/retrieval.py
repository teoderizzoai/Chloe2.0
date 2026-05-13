from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("memory.retrieval")

DEFAULT_MIX: dict[str, int] = {
    "episodic": 12,
    "semantic": 4,
    "autobiographical": 2,
    "procedural": 2,
}

ANCHOR_BONUS = 0.05
INSIDE_JOKE_BONUS = 0.12


@dataclass
class Memory:
    id: int
    kind: str
    text: str
    source: str | None = None
    source_ref: str | None = None
    weight: float = 1.0
    salience: float = 0.5
    confidence: float = 1.0
    emotional_valence: float | None = None
    emotional_arousal: float | None = None
    tags: list = field(default_factory=list)
    artifact_refs: list = field(default_factory=list)
    created_at: str = ""
    archived_tier: str = "hot"
    score: float = 0.0
    relevance_note: str = ""
    confidential_to: int | None = None


def query_fast(
    rich_q: str,
    n: int = 12,
    collection_name: str = "memories_v2",
    rerank: bool = False,
    rerank_keep: int = 5,
    rerank_timeout: float = 0.3,
) -> list[Memory]:
    """Single Chroma query, no per-kind partitioning. Used on the chat hot path.

    `query_mixed` runs one Chroma query per kind (4x embedding cost). For chat
    where we just want the top-N most relevant memories regardless of kind,
    this is ~4x faster.

    Args:
        rerank: If True, run a Flash reranker with a `rerank_timeout` second
                deadline after the Chroma pass. Falls back to score order on
                timeout or error. Adds ~300ms latency but improves precision for
                high-stakes turns (deliberation, character addendum generation).
        rerank_keep: How many memories to keep after reranking (default 5).
        rerank_timeout: Max seconds to wait for the LLM reranker (default 0.3).
    """
    if not rich_q or not rich_q.strip():
        return []
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(collection_name)
    except Exception as exc:
        log.warning("chroma_unavailable", error=str(exc))
        return []

    total_count = collection.count()
    if total_count == 0:
        return []

    try:
        resp = collection.query(
            query_texts=[rich_q],
            n_results=min(n, total_count),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        log.debug("chroma_fast_query_failed", error=str(exc))
        return []

    ids = resp.get("ids", [[]])[0]
    metas = resp.get("metadatas", [[]])[0]
    distances = resp.get("distances", [[]])[0]

    mem_ids: list[int] = []
    for cid in ids:
        try:
            mem_ids.append(int(cid))
        except (TypeError, ValueError):
            pass

    results = _batch_build_memories(mem_ids, metas, distances)

    if results:
        _apply_anchor_bonus(results)
        _apply_inside_joke_bonus(results, rich_q)
        _apply_affect_bonus(results)
    results.sort(key=lambda m: m.score, reverse=True)

    if rerank and results:
        results = _rerank_with_llm(rich_q, results, keep=rerank_keep, timeout=rerank_timeout)
    else:
        results = _mmr(results, n=min(n, len(results)))

    return results


def _rerank_with_llm(
    query: str,
    candidates: list[Memory],
    keep: int = 5,
    timeout: float = 0.3,
) -> list[Memory]:
    """Run a Flash reranker. Falls back to score order on timeout/error.

    This is synchronous but wraps an async Flash call via run_until_complete;
    it is designed for contexts that are already in a sync frame (chat_api helpers
    called from build_dynamic_suffix). Deliberation paths that are already async
    should call the async version directly.
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # We're inside an async context — can't run_until_complete.
        # Schedule a task and fall back to score order for this request.
        loop.create_task(_rerank_async(query, candidates, keep))
        return candidates[:keep]
    except RuntimeError:
        pass  # no running loop — use run_until_complete

    try:
        new_loop = asyncio.new_event_loop()
        try:
            reranked = new_loop.run_until_complete(
                asyncio.wait_for(_rerank_async(query, candidates, keep), timeout=timeout)
            )
            return reranked
        finally:
            new_loop.close()
    except Exception as exc:
        log.debug("reranker_fallback", error=str(exc))
        return candidates[:keep]


async def _rerank_async(query: str, candidates: list[Memory], keep: int) -> list[Memory]:
    """The actual async Flash rerank call."""
    from chloe.llm.gemini import GeminiClient
    from chloe.llm.schemas import GradeResult

    candidate_lines = "\n".join(
        f"id={m.id} | [{m.kind}] {m.text[:200]}" for m in candidates
    )
    client = GeminiClient()
    result = await client.flash(
        "grade_memories.md",
        {
            "message": query,
            "history": "",
            "affect_label": "",
            "candidates_text": candidate_lines,
            "keep": keep,
        },
        GradeResult,
    )
    if not result:
        return candidates[:keep]

    selected_ids_ordered: list[int] = []
    items = result.get("selected") if isinstance(result, dict) else getattr(result, "selected", [])
    for item in items or []:
        raw_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        try:
            selected_ids_ordered.append(int(raw_id))
        except (TypeError, ValueError):
            pass

    if not selected_ids_ordered:
        return candidates[:keep]

    id_to_mem = {m.id: m for m in candidates}
    reranked = [id_to_mem[i] for i in selected_ids_ordered if i in id_to_mem]
    # Preserve any selected not returned by LLM at the end
    seen = set(selected_ids_ordered)
    for m in candidates:
        if m.id not in seen and len(reranked) < keep:
            reranked.append(m)
    log.debug("memory_reranked", query=query[:60], kept=len(reranked))
    return reranked[:keep]


def query_mixed(
    rich_q: str,
    kinds_mix: dict[str, int] | None = None,
    collection_name: str = "memories_v2",
) -> list[Memory]:
    """
    Query ChromaDB per kind with per-kind quotas, combine results, apply anchor bonus.
    Returns a flat list sorted by score descending.
    """
    if not rich_q or not rich_q.strip():
        return []

    mix = kinds_mix or DEFAULT_MIX

    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(collection_name)
    except Exception as exc:
        log.warning("chroma_unavailable", error=str(exc))
        return []

    seen_ids: set[int] = set()
    results: list[Memory] = []

    total_count = collection.count()
    if total_count == 0:
        return []

    for kind, quota in mix.items():
        if quota <= 0:
            continue
        try:
            n = min(quota, total_count)
            resp = collection.query(
                query_texts=[rich_q],
                n_results=n,
                where={"kind": {"$eq": kind}},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            log.debug("chroma_kind_query_failed", kind=kind, error=str(exc))
            continue

        ids = resp.get("ids", [[]])[0]
        metas = resp.get("metadatas", [[]])[0]
        distances = resp.get("distances", [[]])[0]

        batch_ids: list[int] = []
        batch_metas: list[dict] = []
        batch_dists: list[float] = []
        for cid, meta, dist in zip(ids, metas, distances):
            try:
                mid = int(cid)
            except (TypeError, ValueError):
                continue
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            batch_ids.append(mid)
            batch_metas.append(meta)
            batch_dists.append(dist)

        for mem in _batch_build_memories(batch_ids, batch_metas, batch_dists):
            results.append(mem)

    if results:
        _apply_anchor_bonus(results)
        _apply_inside_joke_bonus(results, rich_q)

    results.sort(key=lambda m: m.score, reverse=True)
    return results


def add_to_chroma(memory_id: int, text: str, kind: str, source: str | None,
                  artifact_refs: list, collection_name: str = "memories_v2",
                  emotional_valence: float | None = None,
                  emotional_arousal: float | None = None) -> None:
    """Insert or update a memory document in ChromaDB."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(collection_name)
        meta: dict = {
            "kind": kind,
            "source": source or "",
            "has_artifact": 1 if artifact_refs else 0,
        }
        if emotional_valence is not None:
            meta["emotional_valence"] = emotional_valence
        if emotional_arousal is not None:
            meta["emotional_arousal"] = emotional_arousal
        collection.upsert(
            ids=[str(memory_id)],
            documents=[text],
            metadatas=[meta],
        )
    except Exception as exc:
        log.warning("chroma_add_failed", memory_id=memory_id, error=str(exc))


def _recency_decay(created_at: str) -> float:
    """Exponential recency decay, half-life ~20 days."""
    if not created_at:
        return 1.0
    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days_old = max(0, (datetime.now(timezone.utc) - ts).days)
        return math.exp(-days_old / 30)
    except Exception:
        return 1.0


def _batch_build_memories(
    ids: list[int],
    metas: list[dict],
    distances: list[float],
) -> list[Memory]:
    """Batch SQLite fetch for all IDs, then apply compound scoring.

    Superseded memories (superseded_by IS NOT NULL) are silently excluded —
    they represent corrected facts and should not surface in retrieval.

    Reference tracking: bumps reference_count and last_referenced_at for every
    returned memory so frequently-surfaced memories can receive a small score
    bonus (+0.03 when reference_count > 3).
    """
    if not ids:
        return []
    conn = get_connection()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM memories WHERE id IN ({placeholders}) AND (superseded_by IS NULL)",
        ids,
    ).fetchall()
    row_by_id = {row["id"]: row for row in rows}

    # Bump reference tracking for all returned memories in one query
    returned_ids = list(row_by_id.keys())
    if returned_ids:
        _bump_reference_counts(conn, returned_ids)

    results: list[Memory] = []
    for mem_id, meta, dist in zip(ids, metas, distances):
        cosine = 1.0 / (1.0 + dist)
        row = row_by_id.get(mem_id)
        if row is None:
            # Either not found or superseded — skip silently
            continue
        salience = float(row["salience"] or 0.5)
        created_at = row["created_at"] or ""
        reference_count = int(row["reference_count"]) if "reference_count" in row.keys() else 0
        score = cosine * salience * _recency_decay(created_at)
        # Small bonus for memories that keep getting surfaced
        if reference_count > 3:
            score += 0.03
        results.append(Memory(
            id=row["id"],
            kind=row["kind"],
            text=row["text"],
            source=row["source"],
            source_ref=row["source_ref"],
            weight=row["weight"],
            salience=salience,
            confidence=row["confidence"],
            emotional_valence=row["emotional_valence"],
            emotional_arousal=row["emotional_arousal"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            artifact_refs=json.loads(row["artifact_refs"]) if row["artifact_refs"] else [],
            created_at=created_at,
            archived_tier=row["archived_tier"],
            score=score,
            confidential_to=row["confidential_to"] if "confidential_to" in row.keys() else None,
        ))
    return results


def _bump_reference_counts(conn, ids: list[int]) -> None:
    """Increment reference_count and set last_referenced_at for all returned memories."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE memories SET reference_count = reference_count + 1, last_referenced_at = ? "
            f"WHERE id IN ({placeholders})",
            [now, *ids],
        )
        conn.commit()
    except Exception:
        pass


def _mmr(candidates: list[Memory], n: int = 8, lambda_: float = 0.6) -> list[Memory]:
    """Maximal Marginal Relevance — trades a little relevance for diversity."""
    if len(candidates) <= n:
        return candidates
    selected = [candidates[0]]
    remaining = list(candidates[1:])
    while len(selected) < n and remaining:
        best = max(
            remaining,
            key=lambda c: lambda_ * c.score
                          - (1 - lambda_) * max(_text_overlap(c, s) for s in selected),
        )
        selected.append(best)
        remaining.remove(best)
    return selected


def _text_overlap(a: Memory, b: Memory) -> float:
    """Jaccard similarity on lowercased word sets — no embedding needed."""
    wa = set(a.text.lower().split())
    wb = set(b.text.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _apply_affect_bonus(memories: list[Memory]) -> None:
    """Boost memories whose emotional valence aligns with Chloe's current affect.

    Mood-congruent recall: low-valence states surface heavier memories;
    positive states surface warmer ones. Max bonus is 0.08 to keep it subtle.
    """
    try:
        from chloe.affect.dims import load as load_affect
        affect = load_affect()
        current_valence = affect.valence
    except Exception:
        return
    for m in memories:
        if m.emotional_valence is None:
            continue
        alignment = 1.0 - abs(m.emotional_valence - current_valence)
        m.score += 0.08 * alignment


def _apply_inside_joke_bonus(memories: list[Memory], query: str) -> None:
    """Add INSIDE_JOKE_BONUS to inside-joke memories when the query overlaps their topic."""
    for m in memories:
        for tag in m.tags:
            if tag.startswith("joke_topic:"):
                topic = tag.removeprefix("joke_topic:")
                if topic.lower() in query.lower():
                    m.score += INSIDE_JOKE_BONUS
                break


def _apply_anchor_bonus(memories: list[Memory]) -> None:
    """Add ANCHOR_BONUS to score for memories whose first artifact still exists."""
    conn = get_connection()
    for mem in memories:
        if not mem.artifact_refs:
            continue
        first = mem.artifact_refs[0]
        ref = first.get("ref") if isinstance(first, dict) else None
        if not ref:
            continue
        row = conn.execute(
            "SELECT exists_ FROM artifact_index WHERE ref = ?", (ref,)
        ).fetchone()
        if row and row["exists_"]:
            mem.score += ANCHOR_BONUS

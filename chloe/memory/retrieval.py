from __future__ import annotations

import json
from dataclasses import dataclass, field

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

    for kind, quota in mix.items():
        if quota <= 0:
            continue
        try:
            resp = collection.query(
                query_texts=[rich_q],
                n_results=quota,
                where={"kind": {"$eq": kind}},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            log.debug("chroma_kind_query_failed", kind=kind, error=str(exc))
            continue

        ids = resp.get("ids", [[]])[0]
        metas = resp.get("metadatas", [[]])[0]
        distances = resp.get("distances", [[]])[0]

        for chroma_id, meta, dist in zip(ids, metas, distances):
            mem_id = int(chroma_id)
            if mem_id in seen_ids:
                continue
            seen_ids.add(mem_id)

            score = 1.0 / (1.0 + dist)
            mem = _build_memory(mem_id, meta, score)
            results.append(mem)

    if results:
        _apply_anchor_bonus(results)
        _apply_inside_joke_bonus(results, rich_q)

    results.sort(key=lambda m: m.score, reverse=True)
    return results


def add_to_chroma(memory_id: int, text: str, kind: str, source: str | None,
                  artifact_refs: list, collection_name: str = "memories_v2") -> None:
    """Insert or update a memory document in ChromaDB."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection(collection_name)
        collection.upsert(
            ids=[str(memory_id)],
            documents=[text],
            metadatas=[{
                "kind": kind,
                "source": source or "",
                "has_artifact": 1 if artifact_refs else 0,
            }],
        )
    except Exception as exc:
        log.warning("chroma_add_failed", memory_id=memory_id, error=str(exc))


def _build_memory(mem_id: int, meta: dict, score: float) -> Memory:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM memories WHERE id = ?", (mem_id,)
    ).fetchone()
    if row is None:
        return Memory(id=mem_id, kind=meta.get("kind", "episodic"), text="",
                      score=score)
    return Memory(
        id=row["id"],
        kind=row["kind"],
        text=row["text"],
        source=row["source"],
        source_ref=row["source_ref"],
        weight=row["weight"],
        salience=row["salience"],
        confidence=row["confidence"],
        emotional_valence=row["emotional_valence"],
        emotional_arousal=row["emotional_arousal"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        artifact_refs=json.loads(row["artifact_refs"]) if row["artifact_refs"] else [],
        created_at=row["created_at"] or "",
        archived_tier=row["archived_tier"],
        score=score,
    )


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

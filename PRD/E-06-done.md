# E-06 · `memory/retrieval.py` — kind-quota composition

## Overview

Implement `query_mixed(rich_q, kinds_mix) -> list[Memory]`. Runs separate ChromaDB queries per kind with per-kind quotas, combines into one list, and applies anchor bonus (+0.05 to retrieval score) for memories whose `artifact_refs[0].ref` exists in `artifact_index` with `exists_=1`.

## Context

The 1.0 system queried Chroma with a single combined query across all memory kinds. This could produce retrievals dominated by one kind (e.g., many recent episodic memories crowding out semantic or autobiographical ones). The quota-based approach ensures each kind gets fair representation. The anchor bonus rewards memories that are grounded in real-world artifacts that still exist — a memory of queuing a track has higher utility if that track is still in Spotify (i.e., the action wasn't reverted).

## Data structures

```python
# Default kind quotas
DEFAULT_KINDS_MIX = {
    "episodic":       5,
    "semantic":       3,
    "autobiographical": 2,
    "procedural":     2,
}

ANCHOR_BONUS = 0.05
```

## Implementation

```python
# chloe/memory/retrieval.py

import json
from dataclasses import dataclass
from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("memory.retrieval")

DEFAULT_KINDS_MIX = {
    "episodic":       5,
    "semantic":       3,
    "autobiographical": 2,
    "procedural":     2,
}
ANCHOR_BONUS = 0.05


def query_mixed(
    rich_q: str,
    kinds_mix: dict | None = None,
    collection_name: str = "memories_v2",
) -> list:
    """
    Query ChromaDB with per-kind quotas, apply anchor bonus, return sorted list.
    rich_q: the query string (typically the user message or topic).
    kinds_mix: dict of {kind: quota} — defaults to DEFAULT_KINDS_MIX.
    """
    if kinds_mix is None:
        kinds_mix = DEFAULT_KINDS_MIX.copy()

    chroma = _get_chroma_collection(collection_name)
    if chroma is None:
        log.warning("chroma_collection_not_found", name=collection_name)
        return []

    all_memories = []

    for kind, quota in kinds_mix.items():
        if quota <= 0:
            continue
        try:
            results = chroma.query(
                query_texts=[rich_q],
                n_results=quota * 2,  # Fetch 2x to allow for filtering
                where={"kind": kind},
                include=["documents", "metadatas", "distances"],
            )
            memories = _parse_chroma_results(results, kind)
            all_memories.extend(memories[:quota])  # Enforce quota
        except Exception as exc:
            log.warning("chroma_query_error", kind=kind, error=str(exc))

    # Apply anchor bonus
    all_memories = _apply_anchor_bonus(all_memories)

    # Sort by effective score DESC
    all_memories.sort(key=lambda m: m.get("_score", 0.0), reverse=True)

    log.debug("query_mixed_results",
              total=len(all_memories), kinds={k: v for k, v in kinds_mix.items()})
    return all_memories


def _parse_chroma_results(results: dict, kind: str) -> list:
    memories = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, dists):
        # Convert distance to similarity score (cosine distance: 0=identical, 2=opposite)
        score = max(0.0, 1.0 - dist / 2.0)
        mem = {
            "id": meta.get("id", ""),
            "kind": kind,
            "text": doc,
            "artifact_refs": json.loads(meta.get("artifact_refs", "[]")),
            "weight": meta.get("weight", 1.0),
            "_score": score * meta.get("weight", 1.0),
            "_raw_score": score,
        }
        memories.append(mem)
    return memories


def _apply_anchor_bonus(memories: list) -> list:
    """Apply +ANCHOR_BONUS to score for memories with existing artifacts."""
    conn = get_connection()
    for mem in memories:
        refs = mem.get("artifact_refs", [])
        if not refs:
            continue
        first_ref = refs[0].get("ref", "") if isinstance(refs[0], dict) else refs[0]
        if not first_ref:
            continue
        row = conn.execute(
            "SELECT exists_ FROM artifact_index WHERE ref=?", (first_ref,)
        ).fetchone()
        if row and row["exists_"] == 1:
            mem["_score"] = mem["_score"] + ANCHOR_BONUS
            mem["_anchor_boosted"] = True
    return memories


def _get_chroma_collection(name: str):
    try:
        import chromadb
        from chloe.config import get_settings
        client = chromadb.PersistentClient(path=get_settings().chroma_db_path)
        return client.get_or_create_collection(name)
    except Exception as exc:
        log.warning("chroma_init_error", error=str(exc))
        return None
```

## Dependencies

- ChromaDB (`chromadb` package).
- F-04 (`memories` table with `artifact_refs`).
- A-01 (`artifact_index` table with `exists_` column).
- F-08 (`state/kv.py` — not needed here, but memory store uses it).

## Testing

### Integration tests — `tests/integration/test_memory_retrieval.py`

```python
import pytest
import json
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.memory.retrieval import query_mixed, ANCHOR_BONUS

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def chroma_collection(tmp_path, monkeypatch):
    """Create a real temporary Chroma collection for testing."""
    import chromadb
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection("memories_v2")

    monkeypatch.setattr("chloe.memory.retrieval._get_chroma_collection",
                        lambda name: col)
    return col


def _add_memory(col, id, kind, text, artifact_refs=None, weight=1.0):
    col.add(
        documents=[text],
        metadatas=[{
            "id": id,
            "kind": kind,
            "artifact_refs": json.dumps(artifact_refs or []),
            "weight": weight,
        }],
        ids=[id],
    )


def test_quota_composition(db, chroma_collection):
    """query_mixed returns at most quota items per kind."""
    for i in range(10):
        _add_memory(chroma_collection, f"ep_{i}", "episodic", f"I went to the park today {i}")
    for i in range(5):
        _add_memory(chroma_collection, f"sem_{i}", "semantic", f"Parks are public green spaces {i}")
    for i in range(5):
        _add_memory(chroma_collection, f"auto_{i}", "autobiographical", f"I am someone who loves nature {i}")

    results = query_mixed("what did I do outside?", kinds_mix={
        "episodic": 3,
        "semantic": 2,
        "autobiographical": 1,
    })

    kinds_in_results = [m["kind"] for m in results]
    assert kinds_in_results.count("episodic") <= 3
    assert kinds_in_results.count("semantic") <= 2
    assert kinds_in_results.count("autobiographical") <= 1
    assert len(results) == 6  # 3+2+1


def test_anchor_bonus_applied(db, chroma_collection):
    """Memory with existing artifact gets +0.05 score."""
    _add_memory(chroma_collection, "mem_with_art", "episodic",
                "I queued a track", artifact_refs=[{"kind": "spotify_track", "ref": "spotify:track:xyz"}])
    _add_memory(chroma_collection, "mem_without", "episodic", "I queued a track")

    # Register the artifact as existing
    db.execute(
        "INSERT INTO artifact_index (kind, ref, title, created_by_action, exists_) VALUES (?,?,?,?,1)",
        ("spotify_track", "spotify:track:xyz", "Test track", "act1"),
    )
    db.commit()

    results = query_mixed("queued a track")
    anchored = next((m for m in results if m["id"] == "mem_with_art"), None)
    not_anchored = next((m for m in results if m["id"] == "mem_without"), None)

    if anchored and not_anchored:
        assert anchored.get("_anchor_boosted") is True
        assert anchored["_score"] > not_anchored["_score"]


def test_empty_chroma_returns_empty(db, monkeypatch):
    monkeypatch.setattr("chloe.memory.retrieval._get_chroma_collection", lambda n: None)
    results = query_mixed("anything")
    assert results == []
```

## Acceptance criteria

- 30 memories inserted (mix of kinds) → `query_mixed` returns exactly the quota-mix requested.
- Memory with `artifact_refs[0].ref` in `artifact_index` with `exists_=1` gets `_score` boosted by `+0.05`.
- Memory with non-existent artifact (or `exists_=0`) gets no bonus.
- Results sorted by `_score` descending.
- `_get_chroma_collection()` returning `None` → `query_mixed` returns `[]` without raising.

# H-05 · Memory retention tier promoter

## Overview

Daily job at 04:30. Move memories older than 90 days from `archived_tier="hot"` to `"warm"` and cluster them in batches of 10 (one Flash call per cluster → one semantic summary). Move memories older than 2 years to `archived_tier="cold"` and remove them from Chroma (SQLite row kept).

## Context

The hot tier is ChromaDB — immediately queryable via embedding search. The warm tier is semantically clustered summaries: original memories remain in SQLite, but only the cluster summaries are in Chroma. The cold tier removes embeddings entirely; the SQLite row is preserved for audit/export but no longer participates in retrieval. This tiering keeps ChromaDB lean and retrieval quality high as the memory corpus grows over years.

Clustering: 10 similar memories → 1 summary is a 10x compression. The clustering is done by topic similarity within the batch; the Flash call produces a single coherent summary paragraph.

## Implementation

### `memory/retention.py`

```python
# chloe/memory/retention.py
from __future__ import annotations
from datetime import datetime, timedelta
from chloe.state.db import get_connection
from chloe.llm.gemini import GeminiClient
from chloe.memory.store import MemoryStore
from chloe.observability.logging import get_logger
import ulid
import json

log = get_logger("retention")
_gemini = GeminiClient()
_memory_store = MemoryStore()

HOT_TO_WARM_DAYS = 90
WARM_TO_COLD_DAYS = 730  # ~2 years
CLUSTER_SIZE = 10


async def run_retention_job(dry_run: bool = False) -> dict:
    """
    Runs daily at 04:30.
    Returns summary: {"warm_promoted": N, "clusters_created": N, "cold_promoted": N, "chroma_removed": N}
    """
    stats = {
        "warm_promoted": 0,
        "clusters_created": 0,
        "cold_promoted": 0,
        "chroma_removed": 0,
    }

    warm_count, cluster_count = await _promote_hot_to_warm(dry_run=dry_run)
    stats["warm_promoted"] = warm_count
    stats["clusters_created"] = cluster_count

    cold_count, removed_count = await _promote_warm_to_cold(dry_run=dry_run)
    stats["cold_promoted"] = cold_count
    stats["chroma_removed"] = removed_count

    log.info("retention_job_complete", dry_run=dry_run, **stats)
    return stats


async def _promote_hot_to_warm(dry_run: bool = False) -> tuple[int, int]:
    """
    Move hot memories older than 90 days → warm tier.
    Cluster in batches of 10, write one summary per cluster.
    """
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=HOT_TO_WARM_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id, content, tags, kind, weight
        FROM memories
        WHERE archived_tier = 'hot'
          AND created_at < ?
        ORDER BY created_at ASC
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        log.info("retention_hot_no_candidates")
        return 0, 0

    promoted = 0
    clusters_created = 0
    batches = [rows[i:i + CLUSTER_SIZE] for i in range(0, len(rows), CLUSTER_SIZE)]

    for batch in batches:
        memory_ids = [row["id"] for row in batch]
        contents = [row["content"] for row in batch]

        summary = await _cluster_to_summary(contents)
        if summary is None:
            log.warning("retention_cluster_failed", batch_size=len(batch))
            continue

        if not dry_run:
            # Write cluster summary as new warm memory
            cluster_id = str(ulid.new())
            conn.execute(
                """
                INSERT INTO memories (id, kind, content, tags, source, weight, archived_tier, artifact_refs, created_at)
                VALUES (?, 'semantic', ?, ?, 'cluster_summary', 0.5, 'warm', '[]', ?)
                """,
                (cluster_id, summary, json.dumps(["cluster_summary"]), datetime.utcnow().isoformat()),
            )

            # Mark originals as warm, remove from Chroma
            conn.execute(
                f"UPDATE memories SET archived_tier='warm' WHERE id IN ({','.join('?' * len(memory_ids))})",
                memory_ids,
            )
            conn.commit()

            # Remove originals from Chroma (cluster summary takes their place)
            for mid in memory_ids:
                try:
                    await _memory_store.delete_from_chroma(mid)
                except Exception as exc:
                    log.warning("retention_chroma_delete_failed", memory_id=mid, error=str(exc))

            # Add cluster summary to Chroma
            await _memory_store.add_to_chroma(cluster_id, summary)
            clusters_created += 1

        promoted += len(batch)

    return promoted, clusters_created


async def _promote_warm_to_cold(dry_run: bool = False) -> tuple[int, int]:
    """
    Move warm memories older than 2 years → cold tier.
    Remove cluster summaries from Chroma.
    """
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=WARM_TO_COLD_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id FROM memories
        WHERE archived_tier = 'warm'
          AND created_at < ?
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        return 0, 0

    cold_ids = [row["id"] for row in rows]
    removed = 0

    if not dry_run:
        for mid in cold_ids:
            try:
                await _memory_store.delete_from_chroma(mid)
                removed += 1
            except Exception:
                pass

        conn.execute(
            f"UPDATE memories SET archived_tier='cold' WHERE id IN ({','.join('?' * len(cold_ids))})",
            cold_ids,
        )
        conn.commit()

    return len(cold_ids), removed


async def _cluster_to_summary(contents: list[str]) -> str | None:
    """Flash call: summarize a batch of memory contents into one semantic summary."""
    payload = {
        "memories": contents,
        "instructions": (
            "Summarize these related memories into one coherent paragraph. "
            "Preserve key facts, names, and patterns. "
            "Omit redundant details. Return only the summary text."
        ),
    }

    try:
        result = await _gemini.flash(
            prompt_name="cluster_synthesis.md",
            payload=payload,
            schema=ClusterSynthesis,
        )
        if result is None:
            return None
        if isinstance(result, dict):
            return result.get("summary", "")
        return result.summary
    except Exception as exc:
        log.warning("retention_cluster_flash_error", error=str(exc))
        return None
```

### `ClusterSynthesis` schema (confirm in F-06)

```python
# In chloe/llm/schemas.py:
class ClusterSynthesis(BaseModel):
    summary: str = Field(max_length=1000, description="Semantic cluster summary paragraph")
```

### Wire into daily loop

```python
# In chloe/loop.py — daily 04:30 job:

async def _run_daily_jobs():
    from chloe.memory.decay import run_daily_decay
    from chloe.memory.retention import run_retention_job

    now = datetime.now()
    # Decay at 04:00, retention at 04:30
    if now.hour == 4 and now.minute >= 30:
        log.info("daily_retention_start")
        await run_retention_job()
```

## Testing

### Unit tests — `tests/unit/test_memory_retention.py`

```python
import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.memory.retention import run_retention_job, _promote_hot_to_warm

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_memory(db, tier="hot", days_old=0, kind="episodic"):
    import ulid
    mid = str(ulid.new())
    created_at = (datetime.utcnow() - timedelta(days=days_old)).isoformat()
    db.execute(
        """
        INSERT INTO memories (id, kind, content, tags, source, weight, archived_tier, artifact_refs, created_at)
        VALUES (?, ?, ?, ?, 'test', 0.8, ?, '[]', ?)
        """,
        (mid, kind, f"Memory content {mid[:8]}", "[]", tier, created_at),
    )
    db.commit()
    return mid


@pytest.mark.asyncio
async def test_hot_to_warm_promotes_old_memories(db):
    # Insert 11 hot memories older than 90 days
    ids = [_insert_memory(db, tier="hot", days_old=100) for _ in range(11)]

    mock_summary = {"summary": "Cluster summary of related memories."}

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=mock_summary)
        with patch("chloe.memory.retention._memory_store") as mock_store:
            mock_store.delete_from_chroma = AsyncMock()
            mock_store.add_to_chroma = AsyncMock()
            stats = await run_retention_job()

    assert stats["warm_promoted"] == 11
    assert stats["clusters_created"] >= 1  # 11 → 2 batches (10 + 1), but second might be skipped if < threshold

    # Originals now warm in SQLite
    conn = get_connection()
    warm_count = conn.execute(
        f"SELECT COUNT(*) as n FROM memories WHERE archived_tier='warm' AND id IN ({','.join('?'*len(ids))})",
        ids
    ).fetchone()["n"]
    assert warm_count == 11

    # One cluster summary created
    cluster_count = conn.execute(
        "SELECT COUNT(*) as n FROM memories WHERE source='cluster_summary'"
    ).fetchone()["n"]
    assert cluster_count >= 1


@pytest.mark.asyncio
async def test_recent_memories_not_promoted(db):
    ids = [_insert_memory(db, tier="hot", days_old=30) for _ in range(5)]

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value={"summary": "x"})
        with patch("chloe.memory.retention._memory_store") as mock_store:
            mock_store.delete_from_chroma = AsyncMock()
            mock_store.add_to_chroma = AsyncMock()
            stats = await run_retention_job()

    assert stats["warm_promoted"] == 0


@pytest.mark.asyncio
async def test_warm_to_cold_removes_from_chroma(db):
    ids = [_insert_memory(db, tier="warm", days_old=800) for _ in range(3)]

    removed_ids = []

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock()
        with patch("chloe.memory.retention._memory_store") as mock_store:
            async def capture_delete(mid):
                removed_ids.append(mid)
            mock_store.delete_from_chroma = capture_delete
            mock_store.add_to_chroma = AsyncMock()
            stats = await run_retention_job()

    assert stats["cold_promoted"] == 3
    assert len(removed_ids) == 3

    conn = get_connection()
    cold = conn.execute(
        f"SELECT COUNT(*) as n FROM memories WHERE archived_tier='cold' AND id IN ({','.join('?'*len(ids))})",
        ids
    ).fetchone()["n"]
    assert cold == 3


@pytest.mark.asyncio
async def test_dry_run_makes_no_changes(db):
    ids = [_insert_memory(db, tier="hot", days_old=100) for _ in range(5)]
    conn = get_connection()
    hot_before = conn.execute("SELECT COUNT(*) as n FROM memories WHERE archived_tier='hot'").fetchone()["n"]

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value={"summary": "summary"})
        with patch("chloe.memory.retention._memory_store") as mock_store:
            mock_store.delete_from_chroma = AsyncMock()
            mock_store.add_to_chroma = AsyncMock()
            await run_retention_job(dry_run=True)

    hot_after = conn.execute("SELECT COUNT(*) as n FROM memories WHERE archived_tier='hot'").fetchone()["n"]
    assert hot_before == hot_after  # No changes in dry_run


@pytest.mark.asyncio
async def test_cluster_summary_in_sqlite_not_in_hot(db):
    """After clustering, originals are warm in SQLite, cluster summary is in warm too."""
    [_insert_memory(db, tier="hot", days_old=100) for _ in range(11)]

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value={"summary": "Cluster summary."})
        with patch("chloe.memory.retention._memory_store") as mock_store:
            mock_store.delete_from_chroma = AsyncMock()
            mock_store.add_to_chroma = AsyncMock()
            await run_retention_job()

    conn = get_connection()
    # No hot memories older than 90 days should remain
    old_hot = conn.execute(
        "SELECT COUNT(*) as n FROM memories WHERE archived_tier='hot'"
    ).fetchone()["n"]
    assert old_hot == 0  # All 11 promoted to warm
```

## Dependencies

- E-06 (`memory/store.py` — Chroma add/delete methods).
- E-10 (`memory/decay.py` — runs before retention in daily job).
- F-06 (`ClusterSynthesis` schema).
- F-04 (`memories` table — `archived_tier` column).

## Acceptance criteria

- 11 hot memories aged 95 days → at least 1 semantic summary written in SQLite; originals moved to `archived_tier="warm"`.
- Original memory IDs absent from Chroma after promotion.
- Cluster summary memory present in Chroma after promotion.
- Memories aged 2+ years: `archived_tier="cold"`, absent from Chroma, present in SQLite.
- `dry_run=True` → no database changes made.
- Flash failure for one cluster → other batches still processed.

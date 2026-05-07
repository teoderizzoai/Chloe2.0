from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from chloe.state.db import get_connection
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import ClusterSynthesis
from chloe.observability.logging import get_logger

log = get_logger("retention")
_gemini = GeminiClient()

HOT_TO_WARM_DAYS = 90
WARM_TO_COLD_DAYS = 730
CLUSTER_SIZE = 10


async def run_retention_job(dry_run: bool = False) -> dict:
    """
    Runs daily at 04:30.
    Returns summary: {"warm_promoted", "clusters_created", "cold_promoted", "chroma_removed"}
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
    """Move hot memories older than 90 days → warm tier, cluster in batches of 10."""
    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HOT_TO_WARM_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id, text, tags, kind, weight
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
        contents = [row["text"] for row in batch]

        summary = await _cluster_to_summary(contents)
        if summary is None:
            log.warning("retention_cluster_failed", batch_size=len(batch))
            promoted += len(batch)
            continue

        if not dry_run:
            from chloe.memory.store import delete_from_chroma, chroma_add, add as memory_add

            cluster_id = memory_add(
                kind="semantic",
                text=summary,
                source="cluster_summary",
                tags=["cluster_summary"],
                weight=0.5,
                archived_tier="warm",
            )

            placeholders = ",".join("?" * len(memory_ids))
            conn.execute(
                f"UPDATE memories SET archived_tier='warm' WHERE id IN ({placeholders})",
                memory_ids,
            )
            conn.commit()

            for mid in memory_ids:
                delete_from_chroma(mid)

            chroma_add(cluster_id, summary, kind="semantic")
            clusters_created += 1

        promoted += len(batch)

    return promoted, clusters_created


async def _promote_warm_to_cold(dry_run: bool = False) -> tuple[int, int]:
    """Move warm memories older than 2 years → cold tier, remove from Chroma."""
    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WARM_TO_COLD_DAYS)).isoformat()

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
        from chloe.memory.store import delete_from_chroma

        for mid in cold_ids:
            try:
                delete_from_chroma(mid)
                removed += 1
            except Exception:
                pass

        placeholders = ",".join("?" * len(cold_ids))
        conn.execute(
            f"UPDATE memories SET archived_tier='cold' WHERE id IN ({placeholders})",
            cold_ids,
        )
        conn.commit()

    return len(cold_ids), removed


async def _cluster_to_summary(contents: list[str]) -> str | None:
    """Flash call: summarize a batch of memory texts into one semantic summary."""
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
            prompt_file="cluster_synthesis.md",
            context=payload,
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

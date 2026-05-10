"""Sleep consolidation — nightly clustering of recent memories into semantic summaries.

PRD §11.4. Runs once a night (~03:00 local). Pulls memories from the last 24h
above a salience floor, groups them by tag overlap (no LLM clustering — keeps
cost and latency down), and asks Flash to synthesize each cluster into one
semantic memory. Optionally produces one "dream fragment" (also Flash) from
the highest-salience cluster.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import ClusterSynthesis

log = get_logger("memory.consolidation")
_gemini = GeminiClient()

WINDOW_HOURS = 24
MIN_SALIENCE = 0.4
MAX_CLUSTERS = 5
MIN_CLUSTER_SIZE = 2


def _load_recent(conn) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()
    rows = conn.execute(
        """SELECT id, kind, text, tags, salience, source
           FROM memories
           WHERE created_at >= ?
             AND salience >= ?
             AND kind = 'episodic'
             AND archived_tier = 'hot'
           ORDER BY salience DESC""",
        (cutoff, MIN_SALIENCE),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d["tags"]) if isinstance(d["tags"], str) else (d["tags"] or [])
        except Exception:
            d["tags"] = []
        out.append(d)
    return out


def _cluster_by_tags(memories: list[dict]) -> list[list[dict]]:
    """Cheap clustering: group memories that share at least one tag.

    Memories without tags become singleton groups (which we drop later if
    they don't reach MIN_CLUSTER_SIZE).
    """
    by_tag: dict[str, list[dict]] = defaultdict(list)
    untagged: list[dict] = []
    for m in memories:
        tags = [t for t in (m.get("tags") or []) if isinstance(t, str)]
        if not tags:
            untagged.append(m)
            continue
        for tag in tags:
            by_tag[tag].append(m)

    seen_ids: set[int] = set()
    clusters: list[list[dict]] = []
    for tag in sorted(by_tag, key=lambda t: -len(by_tag[t])):
        bucket = [m for m in by_tag[tag] if m["id"] not in seen_ids]
        if len(bucket) >= MIN_CLUSTER_SIZE:
            clusters.append(bucket)
            for m in bucket:
                seen_ids.add(m["id"])
        if len(clusters) >= MAX_CLUSTERS:
            break

    if untagged and len(clusters) < MAX_CLUSTERS and len(untagged) >= MIN_CLUSTER_SIZE:
        clusters.append(untagged[: MAX_CLUSTERS])

    return clusters[:MAX_CLUSTERS]


async def _synthesize(memories: list[dict]) -> str | None:
    payload = {
        "memories": "\n".join(f"- {m['text']}" for m in memories),
        "instructions": (
            "These are episodic memories from the last 24 hours that share a theme. "
            "Synthesize them into one coherent semantic memory: what pattern, "
            "lesson, or fact emerges? Be specific. Keep it under 4 sentences."
        ),
    }
    try:
        result = await _gemini.flash("cluster_synthesis.md", payload, ClusterSynthesis)
    except Exception as exc:
        log.warning("consolidation_synth_error", error=str(exc))
        return None
    if not result:
        return None
    if isinstance(result, dict):
        return result.get("summary")
    return getattr(result, "summary", None)


async def consolidate_sleep() -> dict:
    """Nightly: cluster recent episodic memories, write semantic summaries.

    Returns counts: {'clusters', 'summaries_written', 'memories_clustered'}.
    """
    conn = get_connection()
    recent = _load_recent(conn)
    if not recent:
        log.info("consolidation_no_recent")
        return {"clusters": 0, "summaries_written": 0, "memories_clustered": 0}

    clusters = _cluster_by_tags(recent)
    if not clusters:
        log.info("consolidation_no_clusters", recent=len(recent))
        return {"clusters": 0, "summaries_written": 0, "memories_clustered": 0}

    from chloe.memory.store import add as memory_add

    summaries_written = 0
    memories_clustered = 0
    for cluster in clusters:
        summary = await _synthesize(cluster)
        if not summary:
            continue
        member_ids = [m["id"] for m in cluster]
        all_tags: set[str] = set()
        for m in cluster:
            all_tags.update(m.get("tags") or [])
        try:
            memory_add(
                kind="semantic",
                text=summary,
                source="sleep_consolidation",
                tags=sorted(all_tags) + ["consolidated"],
                weight=0.7,
                salience=0.6,
                artifact_refs=[f"memory:{i}" for i in member_ids],
            )
            summaries_written += 1
            memories_clustered += len(cluster)
        except Exception as exc:
            log.warning("consolidation_write_failed", error=str(exc))

    log.info(
        "consolidation_complete",
        clusters=len(clusters),
        summaries_written=summaries_written,
        memories_clustered=memories_clustered,
    )
    return {
        "clusters": len(clusters),
        "summaries_written": summaries_written,
        "memories_clustered": memories_clustered,
    }

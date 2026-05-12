from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from chloe.memory.retrieval import query_fast, Memory
from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("cognitive_retrieval")


@dataclass
class PersonContext:
    person_id: int
    name: str
    attachment_depth: float
    fields: dict[str, str]
    recent_memory_count: int


@dataclass
class CognitiveResult:
    memories: list[Memory]
    person_context: list[PersonContext]
    active_beliefs: list[dict]
    affect_summary: dict[str, Any]
    tensions: list[str]
    gaps: list[dict]
    intent: str
    retrieval_ms: float = 0.0


def retrieve(intent: str, top_k: int = 20) -> CognitiveResult:
    t0 = time.monotonic()

    memories = _fetch_memories(intent, top_k)
    person_ctx = _fetch_person_context()
    beliefs = _fetch_active_beliefs()
    affect = _fetch_affect_summary()
    gaps = _fetch_gaps()
    tensions = _detect_tensions(intent, memories, beliefs)

    result = CognitiveResult(
        memories=memories,
        person_context=person_ctx,
        active_beliefs=beliefs,
        affect_summary=affect,
        tensions=tensions,
        gaps=gaps,
        intent=intent,
        retrieval_ms=round((time.monotonic() - t0) * 1000, 1),
    )

    log.debug(
        "cognitive_retrieve",
        intent=intent[:60],
        memories=len(memories),
        tensions=len(tensions),
        gaps=len(gaps),
        ms=result.retrieval_ms,
    )
    return result


def _fetch_memories(intent: str, top_k: int) -> list[Memory]:
    caps = {"episodic": 12, "semantic": 4, "autobiographical": 2, "procedural": 2}
    candidates = query_fast(intent, n=40)
    counts: dict[str, int] = {}
    results: list[Memory] = []
    for m in candidates:
        if counts.get(m.kind, 0) < caps.get(m.kind, 2):
            results.append(m)
            counts[m.kind] = counts.get(m.kind, 0) + 1
    return results


def _fetch_person_context() -> list[PersonContext]:
    conn = get_connection()
    persons = conn.execute(
        "SELECT id, name, attachment_depth FROM persons WHERE is_active = 1"
    ).fetchall()

    results = []
    for p in persons:
        pid = p["id"]
        fields_rows = conn.execute(
            "SELECT field_name, value FROM person_fields WHERE person_id = ?",
            (pid,),
        ).fetchall()
        fields = {r["field_name"]: r["value"] for r in fields_rows}

        mem_count = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM memories
            WHERE source_ref IN (
                SELECT id FROM actions WHERE person_id = ?
            )
            AND created_at >= datetime('now', '-7 days')
            """,
            (pid,),
        ).fetchone()["cnt"]

        results.append(PersonContext(
            person_id=pid,
            name=p["name"],
            attachment_depth=p["attachment_depth"],
            fields=fields,
            recent_memory_count=mem_count,
        ))

    return results


def _fetch_active_beliefs() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, text, confidence, tags, updated_at
        FROM inner_beliefs
        WHERE archived = 0
          AND confidence >= 0.3
        ORDER BY confidence DESC
        LIMIT 10
        """,
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_affect_summary() -> dict[str, Any]:
    from chloe.state.kv import get as kv_get
    raw = kv_get("affect_checkpoint")
    if not raw:
        return {"valence": 0.0, "arousal": 0.0, "label": "neutral"}
    try:
        return json.loads(raw)
    except Exception:
        return {"valence": 0.0, "arousal": 0.0, "label": "neutral"}


def _fetch_gaps() -> list[dict]:
    try:
        from chloe.initiative.gaps import detect_gaps
        gaps = detect_gaps()
        return [
            {
                "subject": g.subject,
                "description": g.description,
                "priority": g.priority,
                "kind": g.kind,
                "suggested_framing": g.suggested_framing,
            }
            for g in gaps[:3]
        ]
    except Exception as e:
        log.warning("gap_fetch_failed", error=str(e))
        return []


def _detect_tensions(intent: str, memories: list[Memory], beliefs: list[dict]) -> list[str]:
    """Surface real tensions from the inner_tensions table and world_beliefs.contradicts.

    The previous implementation used hardcoded word-pair lists and substring matching,
    which produced false positives and missed semantic oppositions. Now we read from
    the sources that actually track contradictions:

    - inner_tensions: pressure-scored unresolved tensions already known
    - world_beliefs.contradicts: belief pairs flagged by the consistency check
    """
    tensions: list[str] = []
    conn = get_connection()

    # 1. Active inner tensions (highest pressure first)
    rows = conn.execute(
        "SELECT text, pressure FROM inner_tensions WHERE resolved=0 ORDER BY pressure DESC LIMIT 3"
    ).fetchall()
    for r in rows:
        tensions.append(f"Active tension (p={r['pressure']:.2f}): {r['text']}")

    # 2. Belief pairs that contradict each other
    conflict_rows = conn.execute(
        """SELECT wb1.topic, wb1.belief, wb2.belief AS conflicting_belief
           FROM world_beliefs wb1
           JOIN world_beliefs wb2 ON wb1.contradicts = wb2.id
           WHERE wb1.contradicts IS NOT NULL
           LIMIT 2"""
    ).fetchall()
    for r in conflict_rows:
        tensions.append(
            f"Belief tension on '{r['topic']}': "
            f"'{r['belief'][:80]}' vs '{r['conflicting_belief'][:80]}'"
        )

    return tensions[:3]

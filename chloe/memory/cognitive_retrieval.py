from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from chloe.memory.retrieval import query_mixed, Memory
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
    mix = {"episodic": 12, "semantic": 4, "autobiographical": 2, "procedural": 2}
    return query_mixed(intent, mix)


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


CONTRADICTION_PAIRS = [
    (["late", "tired", "exhausted"], ["energetic", "motivated", "excited"]),
    (["avoid", "don't want", "not interested"], ["excited about", "looking forward"]),
    (["sick", "ill", "unwell"], ["working", "gym", "running"]),
]


def _detect_tensions(intent: str, memories: list[Memory], beliefs: list[dict]) -> list[str]:
    tensions = []

    intent_lower = intent.lower()
    belief_texts = " ".join(b.get("text", "") for b in beliefs).lower()
    memory_texts = " ".join(getattr(m, "text", "") for m in memories[:5]).lower()
    recent_context = belief_texts + " " + memory_texts

    for pos_words, neg_words in CONTRADICTION_PAIRS:
        if any(w in intent_lower for w in pos_words) and any(w in recent_context for w in neg_words):
            tensions.append(
                f"Possible tension: intent suggests {pos_words[0]!r} but recent context suggests {neg_words[0]!r}."
            )
        if any(w in intent_lower for w in neg_words) and any(w in recent_context for w in pos_words):
            tensions.append(
                f"Possible tension: intent suggests {neg_words[0]!r} but recent context suggests {pos_words[0]!r}."
            )

    return tensions[:3]

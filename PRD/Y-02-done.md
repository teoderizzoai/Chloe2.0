# Y-02 · Unified Retrieval Engine

## Overview

A single `retrieve(intent, context) -> CognitiveResult` function that queries memories, person models, active beliefs, affect state, and knowledge gaps in one call. The deliberation step and chat system prompt currently query each store ad-hoc; this replaces those scattered calls with one coherent retrieval pass, returning a structured result that includes tensions and gaps alongside raw data.

## Context

As of H-series, the deliberation path calls `query_mixed()` for memories, then separately pulls person context and beliefs. The chat prompt builder has its own retrieval calls. This means the same stores are sometimes queried twice per turn, context is assembled inconsistently, and there's no place that detects **tensions** (contradictions between a new intent and existing beliefs/memories) before the model sees them.

The Unified Retrieval Engine is the single assembly point: one call, one result, consistent every time. It also enables:
- **Tension detection**: compare incoming intent against stored beliefs, flag contradictions.
- **Gap injection**: attach `KnowledgeGap` objects (from Y-01) to the result so deliberation and chat both see them.
- **Result caching**: the same retrieval result is passed to both deliberation and prompt-building, not recomputed.

**When:** Phase Y. Depends on Y-01 (gap detection), memory retrieval (E-series), persons store, inner beliefs.

---

## New module: `memory/cognitive_retrieval.py`

```python
# chloe/memory/cognitive_retrieval.py
from __future__ import annotations

import json
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
    fields: dict[str, str]          # field_name → value
    recent_memory_count: int


@dataclass
class CognitiveResult:
    memories: list[Memory]
    person_context: list[PersonContext]
    active_beliefs: list[dict]
    affect_summary: dict[str, Any]
    tensions: list[str]             # contradiction signals surfaced for the model
    gaps: list[dict]                # KnowledgeGap.asdict() for the top gaps
    intent: str
    retrieval_ms: float = 0.0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def retrieve(intent: str, top_k: int = 20) -> CognitiveResult:
    import time
    t0 = time.monotonic()

    memories = await _fetch_memories(intent, top_k)
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


# ---------------------------------------------------------------------------
# Sub-fetchers
# ---------------------------------------------------------------------------

async def _fetch_memories(intent: str, top_k: int) -> list[Memory]:
    mix = {"episodic": 12, "semantic": 4, "autobiographical": 2, "procedural": 2}
    return await query_mixed(intent, mix)


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
        SELECT id, content, confidence, tags, updated_at
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


# ---------------------------------------------------------------------------
# Tension detection
# ---------------------------------------------------------------------------

def _detect_tensions(intent: str, memories: list[Memory], beliefs: list[dict]) -> list[str]:
    """
    Surface contradictions between the current intent/context and stored beliefs/memories.
    This is keyword-based — a cheap first pass before the LLM sees anything.
    """
    tensions = []

    CONTRADICTION_PAIRS = [
        (["late", "tired", "exhausted"], ["energetic", "motivated", "excited"]),
        (["avoid", "don't want", "not interested"], ["excited about", "looking forward"]),
        (["sick", "ill", "unwell"], ["working", "gym", "running"]),
    ]

    intent_lower = intent.lower()
    belief_texts = " ".join(b.get("content", "") for b in beliefs).lower()
    memory_texts = " ".join(getattr(m, "text", "") for m in memories[:5]).lower()
    recent_context = belief_texts + " " + memory_texts

    for pos_words, neg_words in CONTRADICTION_PAIRS:
        intent_has_pos = any(w in intent_lower for w in pos_words)
        context_has_neg = any(w in recent_context for w in neg_words)
        if intent_has_pos and context_has_neg:
            tensions.append(
                f"Possible tension: intent suggests {pos_words[0]!r} but recent context suggests {neg_words[0]!r}."
            )

        intent_has_neg = any(w in intent_lower for w in neg_words)
        context_has_pos = any(w in recent_context for w in pos_words)
        if intent_has_neg and context_has_pos:
            tensions.append(
                f"Possible tension: intent suggests {neg_words[0]!r} but recent context suggests {pos_words[0]!r}."
            )

    return tensions[:3]     # cap at 3 tensions per retrieval
```

---

## Integration points

### Replace ad-hoc retrieval in `actions/deliberate.py`

```python
# Before (scattered):
memories = await query_mixed(rich_q, mix)
person_ctx = get_person_context(person_id)

# After (unified):
from chloe.memory.cognitive_retrieval import retrieve

cognitive = await retrieve(intent=action.intent)
memories = cognitive.memories
person_ctx = cognitive.person_context
# tensions and gaps are now also available to deliberation
```

### Inject into chat system prompt in `llm/prompts.py`

```python
from chloe.memory.cognitive_retrieval import retrieve, CognitiveResult

async def build_context_block(intent: str) -> str:
    cognitive = await retrieve(intent)
    # Pass cognitive to existing prompt sections instead of re-querying each store
    return _format_cognitive_result(cognitive)
```

### `CognitiveResult` is passed through the call stack

Rather than each layer re-querying, the result is computed once per turn and passed as an argument:

```python
# In loop.py:
cognitive = await retrieve(intent=last_message)
response = await chat_model.reply(message=last_message, cognitive=cognitive)
```

---

## Testing

### `tests/unit/test_cognitive_retrieval.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set
from chloe.memory.cognitive_retrieval import (
    retrieve, _fetch_active_beliefs, _fetch_affect_summary,
    _fetch_person_context, _detect_tensions,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.mark.asyncio
async def test_retrieve_returns_cognitive_result(db):
    with patch("chloe.memory.cognitive_retrieval._fetch_memories", new=AsyncMock(return_value=[])):
        result = await retrieve("I want to check in with Teo")
    assert result.intent == "I want to check in with Teo"
    assert isinstance(result.memories, list)
    assert isinstance(result.tensions, list)
    assert isinstance(result.gaps, list)


def test_fetch_affect_summary_defaults_neutral(db):
    summary = _fetch_affect_summary()
    assert summary["label"] == "neutral"


def test_fetch_affect_summary_reads_checkpoint(db):
    kv_set("affect_checkpoint", json.dumps({"valence": 0.7, "arousal": 0.3, "label": "content"}))
    summary = _fetch_affect_summary()
    assert summary["valence"] == pytest.approx(0.7)
    assert summary["label"] == "content"


def test_fetch_active_beliefs_excludes_archived(db):
    conn = get_connection()
    conn.execute(
        "INSERT INTO inner_beliefs (content, confidence, archived, updated_at)"
        " VALUES ('archived belief', 0.9, 1, datetime('now'))"
    )
    conn.execute(
        "INSERT INTO inner_beliefs (content, confidence, archived, updated_at)"
        " VALUES ('active belief', 0.8, 0, datetime('now'))"
    )
    conn.commit()
    beliefs = _fetch_active_beliefs()
    assert all(not b.get("archived") for b in beliefs)
    assert any("active" in b["content"] for b in beliefs)


def test_tension_detection_fires_on_contradiction():
    from dataclasses import dataclass

    @dataclass
    class FakeMem:
        text: str = "I was really energetic today"

    tensions = _detect_tensions(
        intent="I'm tired and exhausted",
        memories=[FakeMem()],
        beliefs=[{"content": "motivated and excited about the project", "confidence": 0.8}],
    )
    assert len(tensions) > 0


def test_tension_detection_no_false_positives():
    tensions = _detect_tensions(
        intent="I want to go for a run",
        memories=[],
        beliefs=[{"content": "enjoys running in the morning", "confidence": 0.7}],
    )
    assert len(tensions) == 0


@pytest.mark.asyncio
async def test_retrieve_ms_is_populated(db):
    with patch("chloe.memory.cognitive_retrieval._fetch_memories", new=AsyncMock(return_value=[])):
        result = await retrieve("test intent")
    assert result.retrieval_ms >= 0
```

---

## Dependencies

- Y-01 (`initiative/gaps.py` — gap detection).
- E-series memory retrieval (`memory/retrieval.py`).
- `inner_beliefs` table (inner/ module).
- `persons` and `person_fields` tables.
- `affect_checkpoint` KV key (Y-04 will write this; pre-Y-04 it defaults to neutral).

## Acceptance criteria

- `retrieve(intent)` returns a `CognitiveResult` with non-empty structure in <150ms on warm DB.
- Tensions are detected when intent and recent context contain known contradiction pairs.
- No more than 3 tensions or 3 gaps per result.
- Affect checkpoint missing → defaults to `{"valence": 0.0, "arousal": 0.0, "label": "neutral"}` without raising.
- Deliberation step uses `cognitive.memories` instead of calling `query_mixed` directly.
- `retrieval_ms` is always populated in the result.

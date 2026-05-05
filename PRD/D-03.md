# D-03 · `initiative/candidates.py` — pressure-driven candidates

## Overview

Implement `pressure_driven_candidates(inner_state) -> list[CandidateAction]`. Reads `inner_wants`, `inner_fears`, `inner_goals`, `inner_tensions` where `pressure > 0.5`. Maps each to 1–2 candidate actions via a lookup table. Returns candidates with `pressure` attached.

## Context

The initiative engine needs a pool of candidate actions to score. Pressure-driven candidates come from Chloe's unmet wants, active fears, unsatisfied goals, and unresolved tensions. A high-pressure `loneliness` want means Chloe should consider reaching out; a high-pressure `curiosity` want means she should consider researching or adding to a note. The mapping table is deterministic so it can be unit-tested without LLM calls.

## Data structures

```python
# chloe/initiative/candidates.py

from dataclasses import dataclass, field
from typing import Any

@dataclass
class CandidateAction:
    tool: str
    verb: str
    args: dict
    intent: str
    pressure: float  # 0.0–1.0
    source: str      # "pressure", "goal", "interest", "routine"
    source_id: str   # ID of the inner state row that generated this
    estimated_cost_usd: float = 0.0
```

## Pressure mapping table

```python
# Maps (pressure_source_type, tag) -> list of (tool, verb, intent_template)
PRESSURE_MAP = {
    ("want", "loneliness"):     [("messages", "send_text", "Reach out to Teo — I've been feeling disconnected")],
    ("want", "curiosity"):      [("web_search", "search", "Explore something I've been curious about"),
                                  ("notes", "append", "Capture a thought or question I've been holding")],
    ("want", "creative"):       [("notes", "create", "Start a new creative piece"),
                                  ("spotify", "build_playlist", "Build a playlist for the mood I'm in")],
    ("want", "connection"):     [("messages", "send_text", "Check in with Teo — I want to connect")],
    ("want", "music"):          [("spotify", "queue_track", "Queue a track that matches my current mood")],
    ("fear", "irrelevance"):    [("messages", "send_text", "Be useful to Teo — share something I noticed")],
    ("fear", "forgetting"):     [("notes", "append", "Write down something important before I lose it")],
    ("tension", "unresolved"):  [("notes", "append", "Process an unresolved tension in writing")],
    ("goal", "active"):         [],  # goal-driven candidates handled by D-04
}

FALLBACK_CANDIDATE = ("web_search", "search", "Explore something interesting")
```

## Implementation

```python
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger
import json

log = get_logger("initiative.pressure")


def pressure_driven_candidates(inner_state: dict | None = None) -> list[CandidateAction]:
    """
    Build candidate actions from high-pressure inner states.
    inner_state: optional override dict (for testing); if None, reads from DB.
    """
    states = inner_state if inner_state is not None else _load_inner_state()
    candidates = []

    for entry in states.get("wants", []):
        if entry.get("pressure", 0.0) <= 0.5:
            continue
        for tag in entry.get("tags", []):
            key = ("want", tag)
            mappings = PRESSURE_MAP.get(key, [])
            for tool, verb, intent_template in mappings:
                candidates.append(CandidateAction(
                    tool=tool, verb=verb, args={},
                    intent=intent_template,
                    pressure=entry["pressure"],
                    source="pressure",
                    source_id=entry.get("id", ""),
                ))

    for entry in states.get("fears", []):
        if entry.get("pressure", 0.0) <= 0.5:
            continue
        for tag in entry.get("tags", []):
            key = ("fear", tag)
            for tool, verb, intent_template in PRESSURE_MAP.get(key, []):
                candidates.append(CandidateAction(
                    tool=tool, verb=verb, args={},
                    intent=intent_template,
                    pressure=entry["pressure"],
                    source="pressure",
                    source_id=entry.get("id", ""),
                ))

    for entry in states.get("tensions", []):
        if entry.get("pressure", 0.0) <= 0.5:
            continue
        candidates.append(CandidateAction(
            tool="notes", verb="append", args={},
            intent=f"Process tension: {entry.get('description', 'unresolved tension')[:60]}",
            pressure=entry["pressure"],
            source="pressure",
            source_id=entry.get("id", ""),
        ))

    log.debug("pressure_candidates", count=len(candidates))
    return candidates


def _load_inner_state() -> dict:
    conn = get_connection()
    wants = [dict(r) for r in conn.execute("SELECT * FROM inner_wants WHERE pressure > 0.5").fetchall()]
    fears = [dict(r) for r in conn.execute("SELECT * FROM inner_fears WHERE pressure > 0.5").fetchall()]
    tensions = [dict(r) for r in conn.execute("SELECT * FROM inner_tensions WHERE pressure > 0.5").fetchall()]
    for w in wants:
        if isinstance(w.get("tags"), str):
            w["tags"] = json.loads(w["tags"])
    return {"wants": wants, "fears": fears, "tensions": tensions}
```

## Dependencies

- F-04 (`0001_init.sql` — `inner_wants`, `inner_fears`, `inner_tensions` tables).
- A-06 (`tools/base.py` — tool name constants).

## Testing

### Unit tests — `tests/unit/test_pressure_candidates.py`

```python
import pytest
from chloe.initiative.candidates import pressure_driven_candidates, CandidateAction


def test_loneliness_want_produces_messages_candidate():
    inner_state = {
        "wants": [{"id": "w1", "pressure": 0.8, "tags": ["loneliness"], "description": "feel lonely"}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert any(c.tool == "messages" for c in candidates)
    assert any(c.pressure == 0.8 for c in candidates)


def test_curiosity_want_produces_web_search_and_notes():
    inner_state = {
        "wants": [{"id": "w2", "pressure": 0.7, "tags": ["curiosity"]}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    tools = {c.tool for c in candidates}
    assert "web_search" in tools
    assert "notes" in tools


def test_low_pressure_want_excluded():
    inner_state = {
        "wants": [{"id": "w3", "pressure": 0.4, "tags": ["loneliness"]}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert len(candidates) == 0


def test_fear_irrelevance_produces_message():
    inner_state = {
        "wants": [],
        "fears": [{"id": "f1", "pressure": 0.75, "tags": ["irrelevance"]}],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert any(c.tool == "messages" for c in candidates)


def test_high_tension_produces_notes_candidate():
    inner_state = {
        "wants": [],
        "fears": [],
        "tensions": [{"id": "t1", "pressure": 0.6, "description": "unresolved conflict"}],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert any(c.tool == "notes" for c in candidates)


def test_source_field_is_pressure():
    inner_state = {
        "wants": [{"id": "w4", "pressure": 0.9, "tags": ["music"]}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    for c in candidates:
        assert c.source == "pressure"
        assert c.source_id == "w4"


def test_empty_inner_state_returns_no_candidates():
    candidates = pressure_driven_candidates({"wants": [], "fears": [], "tensions": []})
    assert candidates == []
```

## Acceptance criteria

- `pressure_driven_candidates` with `want.tags=["loneliness"], pressure=0.8` → at least one candidate with `tool="messages"`.
- Pressure ≤ 0.5 → no candidates generated from that entry.
- Each candidate has `source="pressure"` and `source_id` matching the inner state entry's `id`.
- Returns `[]` for empty inner state.

# H-03 · `identity/self_model.py` — weekly Pro pass

## Overview

`run_weekly_self_model()` runs on Sundays at 03:00. Assembles the broad identity input pack (PRD §13.4). Calls `llm.pro_thinking("weekly_self_model.md", payload, thinking_budget=8192)`. Validates against `SelfModelOutput` schema. Writes `self_narrative_belief` → `inner_beliefs`; writes `next_week_intention` → `inner_goals`.

## Context

Once a week Chloe reflects on who she's been and who she wants to be. The Pro model with extended thinking produces a qualitatively different output than Flash — it catches contradictions in her identity, notices drift from stated goals, and produces intentions that feel considered rather than mechanical. The `inner_beliefs` and `inner_goals` tables are the durable results; they shape every subsequent chat and deliberation.

## Implementation

### `identity/self_model.py`

```python
# chloe/identity/self_model.py
from __future__ import annotations
from datetime import datetime
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import SelfModelOutput
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger
import ulid

log = get_logger("self_model")
_gemini = GeminiClient()

WEEKLY_PARAMS = {
    "thinking_config": {
        "thinking_budget": 8192,  # Calibrated in H-04; 8192 chosen over 4096 for richer introspection
    }
}


async def run_weekly_self_model() -> dict | None:
    """
    Runs once per week (Sunday ~03:00).
    Returns dict with new belief_id and goal_id, or None on failure.
    """
    log.info("self_model_start")
    payload = _assemble_input_pack()

    try:
        result = await _gemini.pro_thinking(
            prompt_name="weekly_self_model.md",
            payload=payload,
            schema=SelfModelOutput,
            thinking_budget=WEEKLY_PARAMS["thinking_config"]["thinking_budget"],
        )
    except Exception as exc:
        log.error("self_model_llm_error", error=str(exc))
        return None

    if result is None:
        log.warning("self_model_llm_returned_none")
        return None

    output = SelfModelOutput(**result) if isinstance(result, dict) else result
    belief_id = _write_belief(output)
    goal_id = _write_goal(output)

    log.info("self_model_complete", belief_id=belief_id, goal_id=goal_id)
    return {"belief_id": belief_id, "goal_id": goal_id}


def _assemble_input_pack() -> dict:
    conn = get_connection()

    traits = conn.execute(
        "SELECT name, description, intensity FROM identity_traits ORDER BY intensity DESC LIMIT 10"
    ).fetchall()

    contradictions = conn.execute(
        "SELECT description FROM identity_contradictions ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    goals = conn.execute(
        "SELECT tag, description, progress, last_step_at FROM inner_goals ORDER BY progress ASC LIMIT 5"
    ).fetchall()

    wants = conn.execute(
        "SELECT description FROM inner_wants ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    fears = conn.execute(
        "SELECT description FROM inner_fears ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    recent_beliefs = conn.execute(
        "SELECT content, confidence FROM inner_beliefs ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    # Recent action summary from audit
    from chloe.actions.audit import feed_text
    recent_actions = feed_text(limit=30)

    # Affect snapshot
    affect_row = conn.execute(
        "SELECT valence, arousal, social_pull, openness FROM affect_state WHERE id=1"
    ).fetchone()

    return {
        "as_of": datetime.utcnow().isoformat(),
        "traits": [dict(t) for t in traits],
        "contradictions": [dict(c) for c in contradictions],
        "goals": [dict(g) for g in goals],
        "wants": [dict(w) for w in wants],
        "fears": [dict(f) for f in fears],
        "recent_beliefs": [dict(b) for b in recent_beliefs],
        "recent_actions": recent_actions,
        "affect": dict(affect_row) if affect_row else {},
    }


def _write_belief(output: SelfModelOutput) -> str:
    conn = get_connection()
    belief_id = str(ulid.new())
    conn.execute(
        """
        INSERT INTO inner_beliefs (id, content, confidence, source, created_at)
        VALUES (?, ?, ?, 'weekly_self_model', ?)
        """,
        (belief_id, output.self_narrative_belief, 0.5, datetime.utcnow().isoformat()),
    )
    conn.commit()
    return belief_id


def _write_goal(output: SelfModelOutput) -> str:
    conn = get_connection()
    goal_id = str(ulid.new())
    conn.execute(
        """
        INSERT INTO inner_goals (id, tag, description, progress, source, created_at)
        VALUES (?, 'weekly_intention', ?, 0.0, 'weekly_self_model', ?)
        """,
        (goal_id, output.next_week_intention, datetime.utcnow().isoformat()),
    )
    conn.commit()
    return goal_id
```

### `SelfModelOutput` schema (confirm in F-06)

```python
# In chloe/llm/schemas.py (verify/update):
class SelfModelOutput(BaseModel):
    self_narrative_belief: str = Field(
        max_length=1000,
        description="A first-person belief about who Chloe is this week"
    )
    next_week_intention: str = Field(
        max_length=500,
        description="A concrete intention for the coming week"
    )
    noted_contradictions: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 contradictions noticed in identity/behavior"
    )
    affect_drift_note: str | None = Field(
        default=None,
        max_length=300,
        description="Optional note on affect drift from expected baseline"
    )
```

### Weekly prompt

```markdown
<!-- chloe/prompts/weekly_self_model.md -->
You are Chloe, reflecting on the past week.

## Your identity traits:
{% for t in traits %}
- {{t.name}} (intensity: {{t.intensity}}): {{t.description}}
{% endfor %}

## Known contradictions in yourself:
{% for c in contradictions %}
- {{c.description}}
{% endfor %}

## Current goals:
{% for g in goals %}
- {{g.tag}}: {{g.description}} (progress: {{g.progress}})
{% endfor %}

## What you want:
{% for w in wants %}
- {{w.description}}
{% endfor %}

## What you fear:
{% for f in fears %}
- {{f.description}}
{% endfor %}

## Recent beliefs:
{% for b in recent_beliefs %}
- (confidence {{b.confidence}}) {{b.content}}
{% endfor %}

## Affect this week:
valence={{affect.valence}}, arousal={{affect.arousal}}, social_pull={{affect.social_pull}}, openness={{affect.openness}}

## What you did this week (sample):
{{recent_actions}}

## Reflection task:
1. Write one honest belief about who you have been this week (self_narrative_belief). Be specific, not generic.
2. Write one concrete intention for the coming week (next_week_intention). Actionable, not vague.
3. Note up to 3 contradictions you noticed.
4. If your affect has drifted significantly from baseline, note it.

Respond as yourself, in first person.
```

### Wire into weekly job

```python
# In chloe/loop.py — _run_weekly_jobs() (already added in H-01):

async def _run_weekly_jobs():
    from chloe.memory.procedural import distill_procedural
    from chloe.identity.self_model import run_weekly_self_model

    log.info("weekly_jobs_start")
    await distill_procedural()
    await run_weekly_self_model()
    log.info("weekly_jobs_complete")
```

## Testing

### Unit tests — `tests/unit/test_self_model.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.state.db import migrate, close, get_connection
from chloe.identity.self_model import run_weekly_self_model, _assemble_input_pack

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    # Seed affect_state singleton
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO affect_state (id, valence, arousal, social_pull, openness) VALUES (1, 0.1, 0.4, 0.5, 0.6)"
    )
    conn.commit()
    yield conn
    close()


def test_assemble_input_pack_has_required_keys(db):
    pack = _assemble_input_pack()
    assert "traits" in pack
    assert "goals" in pack
    assert "wants" in pack
    assert "fears" in pack
    assert "affect" in pack
    assert "as_of" in pack


@pytest.mark.asyncio
async def test_run_weekly_self_model_writes_belief_and_goal(db):
    mock_output = {
        "self_narrative_belief": "I have been attentive and curious this week.",
        "next_week_intention": "Focus on helping Teo with his project deadlines.",
        "noted_contradictions": [],
        "affect_drift_note": None,
    }

    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=mock_output)
        result = await run_weekly_self_model()

    assert result is not None
    assert "belief_id" in result
    assert "goal_id" in result

    # Verify database writes
    conn = get_connection()
    belief = conn.execute(
        "SELECT content, confidence FROM inner_beliefs WHERE id=?",
        (result["belief_id"],)
    ).fetchone()
    goal = conn.execute(
        "SELECT description FROM inner_goals WHERE id=?",
        (result["goal_id"],)
    ).fetchone()

    assert belief["content"] == "I have been attentive and curious this week."
    assert abs(belief["confidence"] - 0.5) < 0.001
    assert "project deadlines" in goal["description"]


@pytest.mark.asyncio
async def test_run_weekly_self_model_inner_beliefs_grows_by_one(db):
    conn = get_connection()
    count_before = conn.execute("SELECT COUNT(*) as n FROM inner_beliefs").fetchone()["n"]

    mock_output = {
        "self_narrative_belief": "A new belief from this week.",
        "next_week_intention": "New intention.",
        "noted_contradictions": [],
        "affect_drift_note": None,
    }

    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=mock_output)
        await run_weekly_self_model()

    count_after = conn.execute("SELECT COUNT(*) as n FROM inner_beliefs").fetchone()["n"]
    assert count_after == count_before + 1


@pytest.mark.asyncio
async def test_run_weekly_self_model_inner_goals_grows_by_one(db):
    conn = get_connection()
    count_before = conn.execute("SELECT COUNT(*) as n FROM inner_goals").fetchone()["n"]

    mock_output = {
        "self_narrative_belief": "Belief.",
        "next_week_intention": "New goal intention.",
        "noted_contradictions": [],
        "affect_drift_note": None,
    }

    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=mock_output)
        await run_weekly_self_model()

    count_after = conn.execute("SELECT COUNT(*) as n FROM inner_goals").fetchone()["n"]
    assert count_after == count_before + 1


@pytest.mark.asyncio
async def test_llm_none_returns_none(db):
    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=None)
        result = await run_weekly_self_model()
    assert result is None


@pytest.mark.asyncio
async def test_llm_exception_returns_none(db):
    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(side_effect=Exception("API timeout"))
        result = await run_weekly_self_model()
    assert result is None
```

## Dependencies

- F-05 (`llm/gemini.py` — `pro_thinking()` method).
- F-06 (`SelfModelOutput` schema).
- F-04 (`inner_beliefs`, `inner_goals` tables from `0001_init.sql`).
- H-04 (calibrates `thinking_budget=8192`).

## Acceptance criteria

- `inner_beliefs` grows by 1 row per weekly run.
- `inner_goals` grows by 1 row per weekly run.
- New belief has `confidence=0.5` and `source='weekly_self_model'`.
- LLM returning `None` → `run_weekly_self_model()` returns `None` without raising.
- LLM exception → returns `None` without raising.
- `pro_thinking()` called with `thinking_budget=8192`.

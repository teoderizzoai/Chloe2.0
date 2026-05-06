# D-08 · `initiative/engine.py` — tick, score, idle

## Overview

Implement `tick() -> ActionResult | None` and `realize(candidate) -> Action`. `tick()` assembles candidates (D-03 through D-06), fetches the opportunity vector (D-07), scores each candidate using the 6-term formula from PRD §14.4, selects the top-1 above threshold, runs mutex check, calls `realize()`, submits to `gate.submit()`. If no candidate exceeds threshold: returns `None` (idle).

## Context

`tick()` is called on a schedule (every 1–5 minutes) by the background task loop. It replaces the 1.0 `_fire_event()` dice roll with scored, world-aware candidate selection. The 6-term scoring formula weights: base pressure, opportunity, recency of last same-verb action, time-of-day bonus, budget headroom, and affect alignment. The mutex check prevents two actions of the same tool from executing simultaneously.

## Scoring formula (PRD §14.4)

```
score(c) = (
    c.pressure                          # 0–1, raw urgency
  * opp_vector[c.tool]                  # 0–1, world readiness
  * recency_penalty(c)                  # 0–1, decay for recently fired same verb
  * time_bonus(c, now)                  # 0.5–1.5, time-of-day alignment
  * budget_headroom()                   # 0–1, inverse of throttle_level
  * affect_alignment(c, affect_state)   # 0.8–1.2, affect amplifier
)
```

## Implementation

```python
# chloe/initiative/engine.py

from datetime import datetime, timedelta
from chloe.initiative.candidates import (
    pressure_driven_candidates, goal_driven_candidates,
    interest_driven_candidates, routine_candidates, CandidateAction,
    mark_routine_done,
)
from chloe.initiative.opportunity import get_opportunity_vector
from chloe.actions.budget import throttle_level
from chloe.actions.audit import recent as audit_recent
from chloe.actions.gate import submit as gate_submit
from chloe.actions.schema import Action
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("initiative.engine")

INITIATIVE_THRESHOLD = 0.35  # Overridden by config


def _get_threshold() -> float:
    from chloe.config import get_settings
    return getattr(get_settings(), "initiative_threshold", INITIATIVE_THRESHOLD)


async def tick() -> object | None:
    """Run one initiative tick. Returns gate ActionResult or None if idle."""
    now = datetime.now()
    threshold = _get_threshold()

    # Assemble candidates
    inner_state = _load_inner_state_snapshot()
    candidates = (
        pressure_driven_candidates(inner_state)
        + goal_driven_candidates(inner_state.get("goals"))
        + interest_driven_candidates(inner_state.get("interests"))
        + routine_candidates(now)
    )

    if not candidates:
        log.debug("tick_idle_no_candidates")
        return None

    # Fetch opportunity vector
    opp = await get_opportunity_vector()

    # Score candidates
    recent = audit_recent(n=50)
    affect = _load_affect()

    scored = []
    for c in candidates:
        score = _score_candidate(c, opp, recent, now, affect)
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    log.debug("tick_scored", best_tool=best.tool, best_verb=best.verb,
              best_score=round(best_score, 3), threshold=threshold,
              candidates=len(scored))

    if best_score < threshold:
        log.info("tick_idle", best_score=round(best_score, 3))
        return None

    # Mutex check: no same tool already running
    if _tool_mutex_active(best.tool):
        log.info("tick_mutex", tool=best.tool)
        return None

    action = realize(best, now)
    result = await gate_submit(action)

    if result.executed and best.source == "routine":
        mark_routine_done(best.source_id, now)

    log.info("tick_action_submitted", tool=action.tool, verb=action.verb,
             score=round(best_score, 3), result_executed=result.executed)
    return result


def _score_candidate(c: CandidateAction, opp, recent: list, now: datetime, affect: dict) -> float:
    tool_opp = getattr(opp, c.tool.replace("-", "_"), 0.5)
    recency = _recency_penalty(c, recent, now)
    time_b = _time_bonus(c, now)
    headroom = max(0.0, 1.0 - throttle_level())
    affect_amp = _affect_alignment(c, affect)
    return c.pressure * tool_opp * recency * time_b * headroom * affect_amp


def _recency_penalty(c: CandidateAction, recent: list, now: datetime) -> float:
    """1.0 if this tool+verb hasn't fired recently; decays linearly to 0.3 after 5 min."""
    cutoff = now - timedelta(minutes=5)
    fired_recently = any(
        a.tool == c.tool and a.verb == c.verb
        and a.proposed_at and a.proposed_at > cutoff.isoformat()
        for a in recent
    )
    return 0.3 if fired_recently else 1.0


def _time_bonus(c: CandidateAction, now: datetime) -> float:
    """Context-appropriate time bonus for message sending."""
    if c.tool == "messages":
        h = now.hour
        if 8 <= h <= 10 or 19 <= h <= 22:
            return 1.5
        if 23 <= h or h < 7:
            return 0.3
    return 1.0


def _affect_alignment(c: CandidateAction, affect: dict) -> float:
    """Amplify or dampen based on current affect state."""
    valence = affect.get("valence", 0.0)
    arousal = affect.get("arousal", 0.5)

    # High arousal + high valence → slightly boost outreach
    if c.tool == "messages" and valence > 0.3 and arousal > 0.5:
        return 1.2
    # Low valence → dampen outreach
    if c.tool == "messages" and valence < -0.5:
        return 0.8
    return 1.0


def _tool_mutex_active(tool: str) -> bool:
    running = kv_get("tool_mutex_running", default={})
    return tool in running


def _load_inner_state_snapshot() -> dict:
    """Load all inner state tables for candidate generation."""
    from chloe.initiative.candidates import _load_inner_state, _load_goals, _load_interests
    state = _load_inner_state()
    state["goals"] = _load_goals()
    state["interests"] = _load_interests()
    return state


def _load_affect() -> dict:
    try:
        from chloe.state.db import get_connection
        row = get_connection().execute("SELECT * FROM affect_state LIMIT 1").fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def realize(candidate: CandidateAction, now: datetime | None = None) -> Action:
    """Turn a CandidateAction into a full Action with auth class from the tool registry."""
    from chloe.tools.registry import get_registry
    registry = get_registry()

    tool = registry.get_tool(candidate.tool)
    auth_class = "free"
    if tool and candidate.verb in tool.verbs:
        auth_class = tool.verbs[candidate.verb].auth_class

    return Action(
        tool=candidate.tool,
        verb=candidate.verb,
        args=candidate.args,
        intent=candidate.intent,
        preview=f"[Initiative] {candidate.tool}.{candidate.verb}: {candidate.intent[:60]}",
        authorization=auth_class,
    )
```

## Dependencies

- D-03 through D-06 (candidate functions).
- D-07 (opportunity vector).
- A-05 (`budget.throttle_level()`).
- A-08 (`gate.submit()`).
- A-02 (`Action` schema).
- F-04 (`affect_state` table).

## Testing

### Unit tests — `tests/unit/test_initiative_engine.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from chloe.initiative.engine import tick, realize, _score_candidate
from chloe.initiative.candidates import CandidateAction
from chloe.llm.schemas import OpportunityVector


def _make_candidate(tool="messages", verb="send_text", pressure=0.8, source="routine"):
    return CandidateAction(
        tool=tool, verb=verb, args={},
        intent="Test intent",
        pressure=pressure,
        source=source,
        source_id="test",
    )


def _neutral_opp():
    return OpportunityVector(
        messages=0.8, spotify=0.5, calendar=0.4,
        notes=0.7, web_search=0.7, gmail=0.3, reminders=0.4,
    )


@pytest.mark.asyncio
async def test_tick_below_threshold_returns_none(monkeypatch):
    low_pressure = [_make_candidate(pressure=0.1)]
    monkeypatch.setattr("chloe.initiative.engine.pressure_driven_candidates", lambda s: low_pressure)
    monkeypatch.setattr("chloe.initiative.engine.goal_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.interest_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.routine_candidates", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine.get_opportunity_vector", AsyncMock(return_value=_neutral_opp()))
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.0)
    monkeypatch.setattr("chloe.initiative.engine._load_inner_state_snapshot", lambda: {"goals": [], "interests": []})
    monkeypatch.setattr("chloe.initiative.engine._load_affect", lambda: {})
    monkeypatch.setattr("chloe.initiative.engine.audit_recent", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine._get_threshold", lambda: 0.35)

    result = await tick()
    assert result is None


@pytest.mark.asyncio
async def test_tick_above_threshold_submits_to_gate(monkeypatch):
    high_pressure = [_make_candidate(pressure=0.9, source="routine")]
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))

    monkeypatch.setattr("chloe.initiative.engine.pressure_driven_candidates", lambda s: high_pressure)
    monkeypatch.setattr("chloe.initiative.engine.goal_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.interest_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.routine_candidates", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine.get_opportunity_vector", AsyncMock(return_value=_neutral_opp()))
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.0)
    monkeypatch.setattr("chloe.initiative.engine._load_inner_state_snapshot", lambda: {"goals": [], "interests": []})
    monkeypatch.setattr("chloe.initiative.engine._load_affect", lambda: {})
    monkeypatch.setattr("chloe.initiative.engine.audit_recent", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine._get_threshold", lambda: 0.35)
    monkeypatch.setattr("chloe.initiative.engine.gate_submit", mock_gate)
    monkeypatch.setattr("chloe.initiative.engine._tool_mutex_active", lambda t: False)
    monkeypatch.setattr("chloe.initiative.engine.mark_routine_done", lambda s, n: None)

    result = await tick()
    assert result is not None
    mock_gate.assert_called_once()


@pytest.mark.asyncio
async def test_tick_mutex_blocks_action(monkeypatch):
    high_pressure = [_make_candidate(tool="messages", pressure=0.9)]
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))

    monkeypatch.setattr("chloe.initiative.engine.pressure_driven_candidates", lambda s: high_pressure)
    monkeypatch.setattr("chloe.initiative.engine.goal_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.interest_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.routine_candidates", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine.get_opportunity_vector", AsyncMock(return_value=_neutral_opp()))
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.0)
    monkeypatch.setattr("chloe.initiative.engine._load_inner_state_snapshot", lambda: {"goals": [], "interests": []})
    monkeypatch.setattr("chloe.initiative.engine._load_affect", lambda: {})
    monkeypatch.setattr("chloe.initiative.engine.audit_recent", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine._get_threshold", lambda: 0.35)
    monkeypatch.setattr("chloe.initiative.engine._tool_mutex_active", lambda t: True)  # Mutex active
    monkeypatch.setattr("chloe.initiative.engine.gate_submit", mock_gate)

    result = await tick()
    assert result is None
    mock_gate.assert_not_called()
```

## Acceptance criteria

- 10 candidates all below threshold → `tick()` returns `None`.
- 1 candidate above threshold → gate receives exactly 1 `Action`.
- Tool mutex active → `tick()` returns `None` even for above-threshold candidate.
- `realize(candidate)` returns `Action` with `authorization` from the tool registry's verb definition.

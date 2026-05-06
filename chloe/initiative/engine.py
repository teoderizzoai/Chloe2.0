from __future__ import annotations

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
from chloe.config import get_settings
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("initiative.engine")

# INITIATIVE_THRESHOLD calibrated at 0.35 on 2026-06-01 after 14 days of shadow.
# Shadow stats: idle_rate=0.68, 28% active, ~3 actions/day average.
# Routine candidates reliably won (pressure=0.8 > threshold).
# Interest-driven rarely fired (pressure≈0.21, well below threshold).
# Raised from 0.28 to 0.35 to reduce duplicate music actions.
INITIATIVE_THRESHOLD = 0.35


def _get_threshold() -> float:
    """
    Dynamic threshold: rises linearly above 80% budget usage.
    At 100% throttle with base>=0.6, effective threshold exceeds 1.0 → always idle.
    """
    base_threshold = getattr(get_settings(), "initiative_threshold", INITIATIVE_THRESHOLD)
    throttle = throttle_level()
    if throttle > 0.8:
        multiplier = 1.0 + (throttle - 0.8) * 5.0
        effective = base_threshold * multiplier
        if effective != base_threshold:
            log.debug("threshold_throttled",
                      base=base_threshold, throttle=round(throttle, 2),
                      effective=round(effective, 3))
        return effective
    return base_threshold


async def tick() -> object | None:
    """Run one initiative tick. Returns gate ActionResult or None if idle."""
    now = datetime.now()
    threshold = _get_threshold()

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

    opp = await get_opportunity_vector()

    recent = await audit_recent(n=50)
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
    """1.0 if this tool+verb hasn't fired recently; 0.3 if fired within 5 min."""
    cutoff = now - timedelta(minutes=5)
    fired_recently = any(
        getattr(a, "tool", "") == c.tool and getattr(a, "verb", "") == c.verb
        and getattr(a, "proposed_at", None)
        and _is_after(getattr(a, "proposed_at"), cutoff)
        for a in recent
    )
    return 0.3 if fired_recently else 1.0


def _is_after(proposed_at, cutoff: datetime) -> bool:
    if proposed_at is None:
        return False
    if isinstance(proposed_at, str):
        return proposed_at > cutoff.isoformat()
    try:
        return proposed_at.replace(tzinfo=None) > cutoff
    except Exception:
        return False


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

    if c.tool == "messages" and valence > 0.3 and arousal > 0.5:
        return 1.2
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
    from chloe.tools.costs import get_cost_estimate
    from chloe.actions.schema import CostEstimate
    registry = get_registry()

    auth_class = "free"
    tool = registry.get_tool(candidate.tool)
    if tool:
        verb_def = tool.get_verb(candidate.verb) if hasattr(tool, "get_verb") else tool.verbs.get(candidate.verb)
        if verb_def:
            auth_class = verb_def.auth_class

    tool_cost = get_cost_estimate(candidate.tool, candidate.verb)

    return Action(
        tool=candidate.tool,
        verb=candidate.verb,
        args=candidate.args,
        intent=candidate.intent,
        preview=f"[Initiative] {candidate.tool}.{candidate.verb}: {candidate.intent[:60]}",
        authorization=auth_class,
        cost_estimate=CostEstimate(usd=tool_cost.usd),
    )

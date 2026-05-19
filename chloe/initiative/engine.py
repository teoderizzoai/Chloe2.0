from __future__ import annotations

from datetime import datetime, timedelta

from chloe.initiative.candidates import (
    pressure_driven_candidates, goal_driven_candidates,
    interest_driven_candidates, routine_candidates, CandidateAction,
    mark_routine_done, mark_pressure_attempted,
)
from chloe.initiative.share_queue import share_queue_candidates
from chloe.initiative.curiosity import curiosity_driven_candidates, mark_curiosity_surfaced
from chloe.initiative.opportunity import get_opportunity_vector
from chloe.actions.budget import throttle_level
from chloe.actions.audit import recent as audit_recent
from chloe.actions.gate import submit as gate_submit
from chloe.actions.schema import Action
from chloe.config import get_settings
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger
from chloe.observability.metrics import chloe_initiative_ticks_total
from chloe.observability import live_buffer

log = get_logger("initiative.engine")

DAILY_WEB_SEARCH_CAP = 3
_SEARCH_BUDGET_KV_KEY = "initiative:web_search_count:{date}"


def _web_search_budget_remaining() -> int:
    """Return how many interest-driven web searches are still allowed today."""
    today = datetime.now().date().isoformat()
    count = kv_get(_SEARCH_BUDGET_KV_KEY.format(date=today)) or 0
    return max(0, DAILY_WEB_SEARCH_CAP - int(count))


def _consume_web_search_budget() -> None:
    today = datetime.now().date().isoformat()
    key = _SEARCH_BUDGET_KV_KEY.format(date=today)
    count = kv_get(key) or 0
    kv_set(key, int(count) + 1)


# INITIATIVE_THRESHOLD calibrated at 0.35 on 2026-06-01 after 14 days of shadow.
# Shadow stats: idle_rate=0.68, 28% active, ~3 actions/day average.
# Routine candidates reliably won (pressure=0.8 > threshold).
# Interest-driven rarely fired (pressure≈0.21, well below threshold).
# Raised from 0.28 to 0.35 to reduce duplicate music actions.
INITIATIVE_THRESHOLD = 0.35
# Tools that have no kinetic effect (read-only or local write) get a much lower bar.
FREE_TOOLS = {"web_search", "notes"}
FREE_THRESHOLD_RATIO = 0.40   # 0.35 → 0.14 effective for free tools


def _get_threshold(candidate: "CandidateAction | None" = None) -> float:
    """
    Dynamic threshold: rises linearly above 80% budget usage.
    Free tools (web_search, notes) use a lower base so they fire more readily.
    """
    base_threshold = getattr(get_settings(), "initiative_threshold", INITIATIVE_THRESHOLD)
    if candidate is not None and candidate.tool in FREE_TOOLS:
        base_threshold = base_threshold * FREE_THRESHOLD_RATIO
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
    from chloe.identity.interest_garden import drain_pending_curiosity_questions
    await drain_pending_curiosity_questions()

    now = datetime.now()
    threshold = _get_threshold()  # base threshold; per-candidate threshold applied below

    inner_state = _load_inner_state_snapshot()
    candidates = (
        pressure_driven_candidates(inner_state)
        + goal_driven_candidates(inner_state.get("goals"))
        + interest_driven_candidates(inner_state.get("interests"))
        + routine_candidates(now)
        + curiosity_driven_candidates()
        + share_queue_candidates()
    )

    affect = _load_affect()
    live_buffer.record_affect({
        "valence": affect.get("valence"),
        "arousal": affect.get("arousal"),
        "dominance": affect.get("dominance"),
        "label": affect.get("label"),
        "current_activity": (inner_state or {}).get("current_activity"),
    })

    if not candidates:
        log.debug("tick_idle_no_candidates")
        live_buffer.record_tick({
            "outcome": "idle_no_candidates", "threshold": round(threshold, 3),
            "candidate_count": 0, "best": None, "affect": affect,
        })
        return None

    opp = await get_opportunity_vector()

    recent = await audit_recent(n=50)

    scored = []
    for c in candidates:
        score = _score_candidate(c, opp, recent, now, affect)
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    log.debug("tick_scored", best_tool=best.tool, best_verb=best.verb,
              best_score=round(best_score, 3), threshold=threshold,
              candidates=len(scored))

    best_summary = {
        "tool": best.tool, "verb": best.verb, "intent": best.intent,
        "score": round(best_score, 3), "source": getattr(best, "source", None),
    }

    effective_threshold = _get_threshold(best)
    if best_score < effective_threshold:
        log.info("tick_idle", best_score=round(best_score, 3))
        chloe_initiative_ticks_total.labels(outcome="idle").inc()
        live_buffer.record_tick({
            "outcome": "idle_below_threshold", "threshold": round(effective_threshold, 3),
            "candidate_count": len(scored), "best": best_summary, "affect": affect,
        })
        return None

    if _tool_mutex_active(best.tool):
        log.info("tick_mutex", tool=best.tool)
        chloe_initiative_ticks_total.labels(outcome="idle").inc()
        live_buffer.record_tick({
            "outcome": "mutex_blocked", "threshold": round(threshold, 3),
            "candidate_count": len(scored), "best": best_summary, "affect": affect,
        })
        return None

    action = realize(best, now)

    if action.tool == "messages" and not action.args.get("body", "").strip():
        body = await _compose_message_body(action, inner_state, affect)
        if not body:
            log.warning("tick_compose_failed", intent=best.intent)
            if best.source == "routine":
                mark_routine_done(best.source_id, now)
            return None
        action.args["body"] = body

    # Budget gate: interest-driven web searches capped at 3/day.
    if best.tool == "web_search" and best.source == "interest":
        if _web_search_budget_remaining() <= 0:
            log.info("tick_web_search_budget_exhausted", date=datetime.now().date().isoformat())
            chloe_initiative_ticks_total.labels(outcome="idle").inc()
            return None
        _consume_web_search_budget()

    result = None
    try:
        result = await gate_submit(action)
        chloe_initiative_ticks_total.labels(outcome="action").inc()
        log.info("tick_action_submitted", tool=action.tool, verb=action.verb,
                 score=round(best_score, 3), result_executed=result.executed)
        live_buffer.record_tick({
            "outcome": "action_executed" if result.executed else "action_suppressed",
            "threshold": round(threshold, 3), "candidate_count": len(scored),
            "best": best_summary, "affect": affect,
            "executed": bool(result.executed),
            "error": getattr(result, "error", None),
        })
    finally:
        # Mark source done regardless of gate outcome or exception.
        if best.source == "routine":
            mark_routine_done(best.source_id, now)
        elif best.source == "pressure":
            mark_pressure_attempted(best.source_id, best.tool, best.verb)
        elif best.source == "interest":
            _mark_interest_attempted(best.source_id)
        elif best.source == "curiosity":
            topic = best.source_id.removeprefix("curiosity:")
            mark_curiosity_surfaced(topic)
        elif best.source == "share_queue":
            try:
                from chloe.initiative.share_queue import mark_shared
                mark_shared(int(best.source_id))
            except Exception:
                pass

    return result


def _mark_interest_attempted(interest_id: str) -> None:
    """Stamp last_engaged_at on an interest so it enters a cooldown window."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        conn.execute(
            "UPDATE interest_garden SET last_engaged_at=? WHERE id=?",
            (datetime.now().isoformat(), interest_id),
        )
        conn.commit()
        log.debug("interest_attempted_marked", interest_id=interest_id)
    except Exception as exc:
        log.debug("interest_attempted_mark_failed", error=str(exc))


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


async def _compose_message_body(action: Action, inner_state: dict, affect: dict) -> str | None:
    """Call the LLM to write the actual message body from the action's intent.

    Uses MessageBodyWithDeliberation schema so the model goes through a
    deliberation step (what it almost said) before committing to the body.
    The deliberation field is logged internally and never sent to Teo.
    """
    from chloe.llm.gemini import get_client as get_llm
    from chloe.llm.schemas import MessageBodyWithDeliberation

    wants_summary = "; ".join(
        w.get("text", "")[:60] for w in inner_state.get("wants", [])[:2]
    ) or "nothing specific"

    context = {
        "intent": action.intent,
        "time_of_day": datetime.now().strftime("%H:%M"),
        "day_of_week": datetime.now().strftime("%A"),
        "affect_label": affect.get("label", "neutral"),
        "valence": round(affect.get("valence", 0.0), 2),
        "arousal": round(affect.get("arousal", 0.5), 2),
        "wants_summary": wants_summary,
        "last_chat_seen": kv_get("last_chat_seen", default="unknown"),
    }

    llm = get_llm()
    try:
        result = await llm.flash("compose_message.md", context, schema=MessageBodyWithDeliberation)
        if result is None:
            return None
        if isinstance(result, dict):
            deliberation = result.get("deliberation", "")
            body = result.get("body", "")
        else:
            deliberation = getattr(result, "deliberation", "")
            body = getattr(result, "body", "")
        if deliberation:
            log.info("message_deliberation", intent=action.intent[:60], deliberation=deliberation[:120])
        return body.strip() or None
    except Exception as exc:
        log.warning("compose_message_failed", error=str(exc))
        return None


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

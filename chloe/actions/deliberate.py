from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from chloe.llm.gemini import get_client as get_llm
from chloe.llm.schemas import Verdict
from chloe.actions.audit import recent as audit_recent, feed_text
from chloe.actions.budget import throttle_level as _throttle_level
from chloe.state.kv import get as kv_get
from chloe.observability.logging import get_logger
from chloe.observability.metrics import deliberation_calls_total
from chloe.memory.retrieval import query_mixed

log = get_logger("deliberate")

# Calibrated 2026-05-06 against kinetic-sensitive email send scenarios.
# 256: too brief, rationale often generic
# 512: specific rationale, catches procedural rule violations
# 1024: no meaningful improvement over 512, higher cost
# Chosen: 512.
DELIBERATION_THINKING_BUDGET = 512


def _get_procedural_memories(action) -> list[dict]:
    """Query top-3 procedural memories relevant to this action's tool/verb."""
    q = f"{action.tool} {action.verb} {getattr(action, 'intent', '') or ''}"
    try:
        memories = query_mixed(rich_q=q, kinds_mix={"procedural": 3})
        return [
            {"content": m.text, "confidence": m.weight, "tags": m.tags}
            for m in memories
        ]
    except Exception as exc:
        log.warning("procedural_retrieval_error", error=str(exc))
        return []


async def deliberate(action, context: dict | None = None) -> Verdict | None:
    """
    Run a deliberation LLM call for the proposed action.
    Escalates to pro_thinking when action is kinetic-sensitive AND high-cost.
    Returns Verdict or None (treat as proceed) on LLM failure.
    """
    context = context or {}
    procedural_hits = _get_procedural_memories(action)
    recent_actions = await audit_recent(n=10)
    audit_feed = feed_text(recent_actions, n=10)

    payload = {
        "proposed_action": {
            "tool": action.tool,
            "verb": action.verb,
            "args": action.args,
            "intent": action.intent,
            "preview": action.preview,
            "auth_class": action.authorization,
        },
        "procedural_hits": procedural_hits,
        "recent_audit": audit_feed,
        "budget_throttle": _throttle_level(),
        "time_of_day": datetime.now().strftime("%H:%M"),
        "day_of_week": datetime.now().strftime("%A"),
        "context": context,
        "last_chat_seen": kv_get("last_chat_seen", default="unknown"),
    }

    use_pro = _is_kinetic_sensitive(action) and _high_cost_estimate(action)
    llm = get_llm()

    try:
        if use_pro:
            log.info("deliberate_pro_thinking", action_id=action.id,
                     cost_usd=getattr(getattr(action, "cost_estimate", None), "usd", 0.0))
            deliberation_calls_total.labels(model="pro_thinking").inc()
            result = await llm.pro_thinking(
                prompt_file="deliberate_action.md",
                context=payload,
                schema=Verdict,
                thinking_budget=DELIBERATION_THINKING_BUDGET,
            )
        else:
            log.info("deliberate_flash", action_id=action.id)
            deliberation_calls_total.labels(model="flash").inc()
            result = await llm.flash("deliberate_action.md", payload, schema=Verdict)
    except Exception as exc:
        log.warning("deliberation_llm_error", error=str(exc))
        return None

    if result is None:
        log.warning("deliberation_llm_failed", action_id=action.id)
        return None

    try:
        verdict = Verdict(**result) if isinstance(result, dict) else result
        log.info("deliberation_verdict",
                 action_id=action.id, decision=verdict.decision, reason=verdict.reason[:80])
        return verdict
    except Exception as exc:
        log.warning("deliberation_parse_error", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# D-02 · should_deliberate() heuristic
# ---------------------------------------------------------------------------

def _audit_recent_sync(n: int) -> list:
    """Sync DB query returning minimal action-like objects for heuristic checks."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT tool, verb, authorization, proposed_at FROM actions ORDER BY proposed_at DESC LIMIT ?",
            (n,)
        ).fetchall()
        return [SimpleNamespace(
            tool=r["tool"],
            verb=r["verb"],
            authorization=r["authorization"],
            proposed_at=r["proposed_at"],
        ) for r in rows]
    except Exception:
        return []


_audit_recent = _audit_recent_sync


def should_deliberate(action) -> bool:
    """
    Return True if the gate should run a deliberation LLM call before executing.
    Cheap pure heuristic — no LLM, no DB writes.
    """
    return (
        _is_kinetic_sensitive(action)
        or _budget_near_cap()
        or _recent_outreach_spike()
        or _high_cost_estimate(action)
        or _repeated_same_verb(action)
    )


def _is_kinetic_sensitive(action) -> bool:
    return action.authorization == "kinetic-sensitive"


def _budget_near_cap() -> bool:
    return _throttle_level() > 0.75


def _recent_outreach_spike() -> bool:
    """True if more than 2 kinetic actions in the last 60 minutes."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    recent = _audit_recent(n=50)
    kinetic_recent = [
        a for a in recent
        if getattr(a, "authorization", "") in ("kinetic", "kinetic-sensitive")
        and getattr(a, "proposed_at", None)
        and getattr(a, "proposed_at") > cutoff.isoformat()
    ]
    return len(kinetic_recent) > 2


def _high_cost_estimate(action) -> bool:
    """True if the estimated cost exceeds $0.10."""
    est = getattr(action, "cost_estimate", None)
    if not est:
        return False
    return getattr(est, "usd", 0.0) > 0.10


def _repeated_same_verb(action) -> bool:
    """True if the same tool+verb appeared 3+ times in the last 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent = _audit_recent(n=100)
    same_verb_count = sum(
        1 for a in recent
        if getattr(a, "tool", "") == action.tool
        and getattr(a, "verb", "") == action.verb
        and getattr(a, "proposed_at", None)
        and getattr(a, "proposed_at") > cutoff.isoformat()
    )
    return same_verb_count >= 3

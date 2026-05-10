"""Every-2h reflection — the feedback loop that grows Chloe's inner state.

PRD §15.1. One Flash call. Reads recent chat + affect events + current inner
state, outputs new wants/tensions/interests/goal-progress. Without this, the
inner_* tables stay empty forever and the initiative engine has no candidates.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import ReflectOutput

log = get_logger("reflect.every_2h")
_gemini = GeminiClient()

WINDOW_HOURS = 2
RECENT_CHAT_TURNS = 30
LAST_REFLECT_KEY = "reflect:last_run_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _due() -> bool:
    last = kv_get(LAST_REFLECT_KEY)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return (_now() - last_dt) >= timedelta(hours=WINDOW_HOURS)


def _load_recent_chat(conn, n: int) -> str:
    # Chat is persisted into `memories` (source='chat') by mobile_ws._persist_chat_turn.
    # Each row's text is prefixed with "Teo said:" or "I said:" — strip that to recover role.
    rows = conn.execute(
        "SELECT text, created_at FROM memories WHERE source='chat' ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    if not rows:
        return "(no recent chat)"
    out = []
    for r in reversed(rows):
        body = r["text"] or ""
        if body.startswith("Teo said:"):
            out.append(f"[user] {body[len('Teo said:'):].strip()}")
        elif body.startswith("I said:"):
            out.append(f"[chloe] {body[len('I said:'):].strip()}")
        else:
            out.append(body)
    return "\n".join(out)


def _load_affect_summary(conn) -> str:
    cutoff = (_now() - timedelta(hours=WINDOW_HOURS)).isoformat()
    rows = conn.execute(
        """SELECT trigger, valence_delta, arousal_delta, intensity
           FROM affect_records
           WHERE created_at >= ?
           ORDER BY intensity DESC LIMIT 10""",
        (cutoff,),
    ).fetchall()
    if not rows:
        return "(no notable affect events)"
    return "\n".join(
        f"- {r['trigger']}: v={r['valence_delta']:+.2f} a={r['arousal_delta']:+.2f} i={r['intensity']:.2f}"
        for r in rows
    )


def _load_inner_state(conn) -> dict:
    wants = conn.execute(
        "SELECT text, pressure FROM inner_wants WHERE resolved=0 ORDER BY pressure DESC LIMIT 5"
    ).fetchall()
    fears = conn.execute(
        "SELECT text, pressure FROM inner_fears WHERE resolved=0 ORDER BY pressure DESC LIMIT 5"
    ).fetchall()
    tensions = conn.execute(
        "SELECT text, pressure FROM inner_tensions WHERE resolved=0 ORDER BY pressure DESC LIMIT 5"
    ).fetchall()
    return {
        "wants": "; ".join(f"{r['text']} (p={r['pressure']:.2f})" for r in wants) or "(none)",
        "fears": "; ".join(f"{r['text']} (p={r['pressure']:.2f})" for r in fears) or "(none)",
        "tensions": "; ".join(f"{r['text']} (p={r['pressure']:.2f})" for r in tensions) or "(none)",
    }


def _load_goals(conn) -> str:
    rows = conn.execute(
        """SELECT id, name, progress FROM inner_goals
           WHERE status='active' ORDER BY created_at DESC LIMIT 5"""
    ).fetchall()
    return "\n".join(f"- #{r['id']} {r['name']} (progress {r['progress']:.2f})" for r in rows) or "(none)"


def _load_interests(conn) -> str:
    rows = conn.execute(
        "SELECT label, intensity FROM interest_garden WHERE intensity > 0.05 ORDER BY intensity DESC LIMIT 5"
    ).fetchall()
    return "\n".join(f"- {r['label']} ({r['intensity']:.2f})" for r in rows) or "(none)"


def _load_recent_outcomes(conn) -> str:
    """Return recent autonomous actions and what came back, as readable lines."""
    cutoff = (_now() - timedelta(hours=WINDOW_HOURS)).isoformat()
    rows = conn.execute(
        """SELECT text, created_at FROM memories
           WHERE source='action' AND created_at >= ?
           ORDER BY id DESC LIMIT 10""",
        (cutoff,),
    ).fetchall()
    if not rows:
        return "(none)"
    return "\n".join(f"- {r['text']}" for r in reversed(rows))


def _apply_output(output: ReflectOutput) -> dict:
    """Persist reflect output into inner state tables. Returns counts."""
    from chloe.inner.pressure import add_want, add_tension
    from chloe.identity.interest_garden import add_interest, boost_interest
    from chloe.identity.goals import update_progress, add_goal
    from chloe.memory.store import add as memory_add

    counts = {"wants": 0, "tensions": 0, "interests": 0, "goals": 0, "goal_updates": 0, "world_beliefs": 0}

    for w in output.new_wants:
        try:
            add_want(w.text, tags=w.tags or ["curiosity"], pressure=w.pressure)
            counts["wants"] += 1
        except Exception as exc:
            log.warning("apply_want_failed", error=str(exc))

    for t in output.new_tensions:
        try:
            add_tension(t.text, tags=t.tags or ["unresolved"], pressure=t.pressure)
            counts["tensions"] += 1
        except Exception as exc:
            log.warning("apply_tension_failed", error=str(exc))

    for i in output.new_interests:
        try:
            if not boost_interest(i.label, delta=0.05):
                add_interest(i.label, why=i.why, intensity=i.intensity, category=i.category)
            counts["interests"] += 1
        except Exception as exc:
            log.warning("apply_interest_failed", error=str(exc))

    for ng_raw in (output.new_goals or []):
        try:
            # Defensively handle both model objects and raw dicts from partial parses.
            if isinstance(ng_raw, dict):
                from chloe.llm.schemas import ReflectNewGoal
                ng = ReflectNewGoal.model_validate(ng_raw)
            else:
                ng = ng_raw
            if not getattr(ng, "name", "").strip():
                continue
            add_goal(ng.name.strip(), why=ng.why or None)
            counts["goals"] += 1
        except Exception as exc:
            log.warning("apply_new_goal_failed", error=str(exc))

    for g in output.goal_progress_updates:
        try:
            update_progress(g.goal_id, g.delta, note=g.why)
            counts["goal_updates"] += 1
        except Exception as exc:
            log.warning("apply_goal_update_failed", error=str(exc))

    for wb in (output.new_world_beliefs or []):
        try:
            conn = get_connection()
            conn.execute(
                """INSERT INTO world_beliefs (topic, belief, confidence, source)
                   VALUES (?, ?, ?, 'reflect')
                   ON CONFLICT(topic) DO UPDATE SET
                       belief=excluded.belief,
                       confidence=MAX(confidence, excluded.confidence),
                       updated_at=datetime('now')""",
                (wb.topic.strip()[:80], wb.belief.strip(), wb.confidence),
            )
            conn.commit()
            counts["world_beliefs"] += 1
        except Exception as exc:
            log.warning("apply_world_belief_failed", error=str(exc))

    if output.continuity_note:
        try:
            memory_add(
                kind="autobiographical",
                text=output.continuity_note,
                source="reflect",
                tags=["continuity", "reflect_2h"],
                weight=0.6,
                salience=0.5,
            )
        except Exception as exc:
            log.warning("apply_continuity_failed", error=str(exc))

    if output.recurring_loops:
        try:
            kv_set("reflect:recurring_loops", output.recurring_loops[:5])
        except Exception:
            pass

    return counts


async def run_reflect(force: bool = False) -> dict | None:
    """Run the every-2h reflect pass. Returns summary or None if not due / failed."""
    if not force and not _due():
        return None

    conn = get_connection()
    inner = _load_inner_state(conn)
    payload = {
        "recent_chat": _load_recent_chat(conn, RECENT_CHAT_TURNS),
        "affect_summary": _load_affect_summary(conn),
        "current_wants": inner["wants"],
        "current_fears": inner["fears"],
        "current_tensions": inner["tensions"],
        "goals": _load_goals(conn),
        "interests": _load_interests(conn),
    }

    payload["recent_outcomes"] = _load_recent_outcomes(conn)

    log.info("reflect_start")
    try:
        result = await _gemini.flash("reflect_combined.md", payload, ReflectOutput)
    except Exception as exc:
        log.warning("reflect_llm_error", error=str(exc))
        return None

    if not result:
        log.warning("reflect_llm_returned_none")
        return None

    output = ReflectOutput(**result) if isinstance(result, dict) else result
    counts = _apply_output(output)
    kv_set(LAST_REFLECT_KEY, _now().isoformat())
    log.info("reflect_complete", **counts, note=output.continuity_note[:80])
    return {"applied": counts, "continuity_note": output.continuity_note}


async def reflect_loop():
    """Background coroutine — checks every 5 minutes whether reflect is due."""
    import asyncio
    while True:
        try:
            await run_reflect()
        except Exception as exc:
            log.warning("reflect_loop_error", error=str(exc))
        await asyncio.sleep(300)

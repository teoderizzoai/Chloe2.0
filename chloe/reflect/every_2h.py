"""Every-2h reflection — the feedback loop that grows Chloe's inner state.

PRD §15.1. One Flash call. Reads recent chat + affect events + current inner
state, outputs new wants/tensions/interests/goal-progress. Without this, the
inner_* tables stay empty forever and the initiative engine has no candidates.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import ReflectOutput, ReflectCurrentState, ReflectDevelopmental

log = get_logger("reflect.every_2h")
_gemini = GeminiClient()

WINDOW_HOURS = 2
RECENT_CHAT_TURNS = 12
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


def _has_new_signal(since: str) -> bool:
    """Return True if there is enough new activity to justify a Flash call.

    Criteria (any one is sufficient):
    - ≥3 new memories since the last reflect
    - ≥2 affect records with intensity ≥0.4
    - any autonomous action recorded since then
    """
    conn = get_connection()
    mem_count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE created_at > ? AND source != 'action'",
        (since,),
    ).fetchone()[0]
    if mem_count >= 3:
        return True

    affect_count = conn.execute(
        "SELECT COUNT(*) FROM affect_records WHERE created_at > ? AND intensity >= 0.4",
        (since,),
    ).fetchone()[0]
    if affect_count >= 2:
        return True

    action_count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE created_at > ? AND source='action'",
        (since,),
    ).fetchone()[0]
    return action_count > 0


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
    anticipations = conn.execute(
        "SELECT text, intensity FROM inner_anticipations WHERE resolved=0 ORDER BY intensity DESC LIMIT 8"
    ).fetchall()
    questions = conn.execute(
        "SELECT text, domain FROM inner_questions WHERE resolved=0 ORDER BY intensity DESC LIMIT 10"
    ).fetchall()
    return {
        "wants": "; ".join(f"{r['text']} (p={r['pressure']:.2f})" for r in wants) or "(none)",
        "fears": "; ".join(f"{r['text']} (p={r['pressure']:.2f})" for r in fears) or "(none)",
        "tensions": "; ".join(f"{r['text']} (p={r['pressure']:.2f})" for r in tensions) or "(none)",
        "current_anticipations": "\n".join(f"- {r['text']} (i={r['intensity']:.2f})" for r in anticipations) or "(none)",
        "current_questions": "\n".join(f"- [{r['domain']}] {r['text']}" for r in questions) or "(none)",
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


def _load_world_beliefs(conn) -> str:
    rows = conn.execute(
        """SELECT topic, belief, confidence, noticing
           FROM world_beliefs ORDER BY confidence DESC LIMIT 10"""
    ).fetchall()
    if not rows:
        return "(none yet)"
    lines = []
    for r in rows:
        conf = float(r["confidence"] or 0.0)
        prefix = "noticing" if r["noticing"] else ("thinking" if conf < 0.5 else "believe")
        lines.append(f"- [{prefix}] {r['topic']}: {r['belief']}")
    return "\n".join(lines)


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


async def _apply_output(output: ReflectOutput) -> dict:
    """Persist reflect output into inner state tables. Returns counts."""
    from chloe.inner.pressure import add_want, add_tension
    from chloe.identity.interest_garden import add_interest, boost_interest
    from chloe.identity.goals import update_progress, add_goal
    from chloe.memory.store import add as memory_add

    counts = {"wants": 0, "tensions": 0, "interests": 0, "goals": 0, "goal_updates": 0, "world_beliefs": 0, "trait_evidence": 0, "anticipations": 0, "questions": 0}

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
            from chloe.inner.belief_revision import store_new_belief
            await store_new_belief(
                topic=wb.topic.strip()[:80],
                belief=wb.belief.strip(),
                proposed_confidence=wb.confidence,
                noticing=bool(getattr(wb, "noticing", False)),
            )
            counts["world_beliefs"] += 1
        except Exception as exc:
            log.warning("apply_world_belief_failed", error=str(exc))

    for te in (output.trait_evidence or []):
        try:
            from chloe.identity.trait_model import record_trait_evidence
            record_trait_evidence(
                behavior_observed=te.behavior_observed,
                trait_implied=te.trait_implied,
                reinforces=te.reinforces,
                contradicts=te.contradicts,
            )
            counts["trait_evidence"] += 1
        except Exception as exc:
            log.warning("apply_trait_evidence_failed", error=str(exc))

    if output.continuity_note:
        try:
            memory_add(
                kind="autobiographical",
                text=output.continuity_note,
                source="reflect",
                tags=["continuity", "reflect_2h"],
                weight=0.6,
                salience=0.5,
                emotional_labels=output.current_emotions or [],
            )
        except Exception as exc:
            log.warning("apply_continuity_failed", error=str(exc))

    if output.recurring_loops:
        try:
            kv_set("reflect:recurring_loops", output.recurring_loops[:5])
        except Exception:
            pass

    if output.biased_summary:
        try:
            kv_set("reflect:biased_summary", output.biased_summary)
        except Exception:
            pass

    if output.current_emotions:
        try:
            kv_set("reflect:current_emotions", output.current_emotions)
            log.info("reflect_current_emotions", emotions=output.current_emotions)
        except Exception:
            pass

    for ant in (output.new_anticipations or []):
        try:
            conn = get_connection()
            conn.execute(
                """INSERT INTO inner_anticipations (text, valence, intensity, target_date)
                   VALUES (?, ?, ?, ?)""",
                (ant.text.strip(), ant.valence, ant.intensity, ant.target_date),
            )
            conn.commit()
            counts["anticipations"] += 1
        except Exception as exc:
            log.warning("apply_anticipation_failed", error=str(exc))

    for q in (output.new_questions or []):
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO inner_questions (text, domain, intensity) VALUES (?, ?, ?)",
                (q.text.strip(), q.domain or "world", q.intensity),
            )
            conn.commit()
            counts["questions"] += 1
        except Exception as exc:
            log.warning("apply_question_failed", error=str(exc))

    # Novelty signal: decrement deficit when new content surfaces
    new_content = counts["wants"] + counts["interests"] + counts["world_beliefs"] + counts["questions"]
    if new_content > 0:
        try:
            deficit = float(kv_get("affect:novelty_deficit") or 0.0)
            kv_set("affect:novelty_deficit", max(0.0, deficit - 0.1 * new_content))
        except Exception:
            pass

    return counts


def _decay_old_anticipations(conn) -> int:
    """Reduce intensity on unresolved anticipations older than 48h; archive those that fade to near-zero.

    Each reflect cycle, anticipations that have been sitting for more than 2 days
    lose a small amount of intensity. If intensity drops below 0.1 they are
    archived (resolved=1) so they stop accumulating on the inner page.
    """
    cutoff = (_now() - timedelta(hours=48)).isoformat()
    rows = conn.execute(
        "SELECT id, intensity FROM inner_anticipations WHERE resolved=0 AND created_at < ?",
        (cutoff,),
    ).fetchall()
    decayed = 0
    for row in rows:
        new_intensity = round(float(row["intensity"]) - 0.04, 4)
        if new_intensity < 0.1:
            conn.execute("UPDATE inner_anticipations SET resolved=1 WHERE id=?", (row["id"],))
        else:
            conn.execute("UPDATE inner_anticipations SET intensity=? WHERE id=?", (new_intensity, row["id"]))
        decayed += 1
    if decayed:
        conn.commit()
        log.info("anticipations_decayed", count=decayed)
    return decayed


def _harvest_person_moments(conn, since: str) -> int:
    """Promote high-salience episodic memories into person_moments.

    Runs after each reflect pass. Only memories linked to a specific person
    (subject_person_id set) with salience ≥ 0.7 qualify — these are the
    memorable, notable exchanges: shared discoveries, inside jokes, strong
    reactions. Capped at 200 per person to stay readable.
    """
    rows = conn.execute(
        """SELECT subject_person_id, text, created_at
           FROM memories
           WHERE subject_person_id IS NOT NULL
             AND kind = 'episodic'
             AND salience >= 0.7
             AND created_at > ?
           ORDER BY created_at""",
        (since,),
    ).fetchall()

    inserted = 0
    for row in rows:
        pid = row["subject_person_id"]
        text = (row["text"] or "").strip()
        if len(text) < 10:
            continue
        exists = conn.execute(
            "SELECT 1 FROM person_moments WHERE person_id=? AND text=?",
            (pid, text),
        ).fetchone()
        if exists:
            continue
        count = conn.execute(
            "SELECT COUNT(*) FROM person_moments WHERE person_id=?", (pid,)
        ).fetchone()[0]
        if count >= 200:
            continue
        conn.execute(
            "INSERT INTO person_moments (person_id, text, created_at) VALUES (?, ?, ?)",
            (pid, text, row["created_at"]),
        )
        inserted += 1

    if inserted:
        conn.commit()
    return inserted


async def run_reflect(force: bool = False) -> dict | None:
    """Run the every-2h reflect pass. Returns summary or None if not due / failed."""
    if not force and not _due():
        return None

    # Skip the Flash call when nothing noteworthy happened since last reflect.
    # Force=True (post-chat reflect) bypasses this check — the caller already
    # knows there was a conversation.
    if not force:
        last_ts = kv_get(LAST_REFLECT_KEY) or "1970-01-01T00:00:00+00:00"
        if not _has_new_signal(last_ts):
            log.info("reflect_skipped_no_signal", last=last_ts)
            kv_set(LAST_REFLECT_KEY, _now().isoformat())  # advance timer
            # Nothing new — novelty deficit rises
            try:
                deficit = float(kv_get("affect:novelty_deficit") or 0.0)
                kv_set("affect:novelty_deficit", min(1.0, deficit + 0.05))
            except Exception:
                pass
            return {"applied": {}, "skipped": "no_signal"}

    conn = get_connection()
    inner = _load_inner_state(conn)
    payload = {
        "recent_chat": _load_recent_chat(conn, RECENT_CHAT_TURNS),
        "affect_summary": _load_affect_summary(conn),
        "current_wants": inner["wants"],
        "current_fears": inner["fears"],
        "current_tensions": inner["tensions"],
        "current_anticipations": inner["current_anticipations"],
        "current_questions": inner["current_questions"],
        "goals": _load_goals(conn),
        "interests": _load_interests(conn),
    }

    payload["recent_outcomes"] = _load_recent_outcomes(conn)
    payload["world_beliefs"] = _load_world_beliefs(conn)

    log.info("reflect_start")

    # --- Pass 1: Router — is there anything worth reflecting on? ---
    # Force=True (post-chat) skips the router since we know there was a conversation.
    if not force:
        try:
            from chloe.llm.schemas import ReflectRouterOutput
            router_payload = {
                "recent_chat": payload["recent_chat"],
                "affect_summary": payload["affect_summary"],
                "recent_outcomes": payload["recent_outcomes"],
            }
            router_result = await _gemini.flash("reflect_router.md", router_payload, ReflectRouterOutput)
            if router_result:
                noteworthy = (
                    router_result.get("noteworthy") if isinstance(router_result, dict)
                    else getattr(router_result, "noteworthy", True)
                )
                if not noteworthy:
                    log.info("reflect_router_skipped", summary=str(router_result.get("summary", "") if isinstance(router_result, dict) else ""))
                    kv_set(LAST_REFLECT_KEY, _now().isoformat())
                    # Nothing noteworthy — novelty deficit rises
                    try:
                        deficit = float(kv_get("affect:novelty_deficit") or 0.0)
                        kv_set("affect:novelty_deficit", min(1.0, deficit + 0.05))
                    except Exception:
                        pass
                    return {"applied": {}, "skipped": "router_nothing_noteworthy"}
        except Exception as exc:
            log.warning("reflect_router_error", error=str(exc))
            # Fall through to the full reflect on router failure

    # --- Pass 2: Two parallel specialist Flash calls replace the single 12-field monolith.
    #
    # reflect_inner_state.md → ReflectCurrentState: felt inner-state changes
    #   (wants, tensions, loops, biased_summary, anticipations, questions)
    # reflect_signals.md → ReflectDevelopmental: slower developmental signals
    #   (interests, goals, beliefs, trait_evidence)
    #
    # Running concurrently halves the wait vs. sequential. Each call is independently
    # robust — JSON dropout in one doesn't silence the other.
    inner_payload = {k: v for k, v in payload.items()
                     if k in ("recent_chat", "affect_summary", "recent_outcomes",
                              "current_wants", "current_fears", "current_tensions",
                              "current_anticipations", "current_questions")}
    signal_payload = {k: v for k, v in payload.items()
                      if k in ("recent_chat", "goals", "interests", "world_beliefs",
                               "affect_summary", "recent_outcomes")}

    # Cache payloads for debug view
    try:
        kv_set("debug:last_reflect_inner_payload", inner_payload)
        kv_set("debug:last_reflect_signal_payload", signal_payload)
    except Exception:
        pass

    try:
        inner_task = _gemini.flash("reflect_inner_state.md", inner_payload, ReflectCurrentState)
        signal_task = _gemini.flash("reflect_signals.md", signal_payload, ReflectDevelopmental)
        inner_result, signal_result = await asyncio.gather(inner_task, signal_task)
    except Exception as exc:
        log.warning("reflect_llm_error", error=str(exc))
        return None

    # Cache results for debug view
    try:
        def _to_dict(r):
            if r is None: return {}
            if isinstance(r, dict): return r
            try: return r.model_dump()
            except Exception: return str(r)
        kv_set("debug:last_reflect_inner_result", _to_dict(inner_result))
        kv_set("debug:last_reflect_signal_result", _to_dict(signal_result))
    except Exception:
        pass

    if not inner_result and not signal_result:
        chat_snippet = (payload.get("recent_chat") or "")[:200]
        affect_snippet = (payload.get("affect_summary") or "")[:100]
        log.warning("reflect_both_llm_failed",
                    recent_chat_snippet=chat_snippet,
                    affect_summary_snippet=affect_snippet)
        return None

    # Merge both results into a single ReflectOutput for _apply_output
    merged: dict = {
        "continuity_note": "",
        "new_wants": [], "new_tensions": [], "new_interests": [],
        "new_goals": [], "goal_progress_updates": [], "new_world_beliefs": [],
        "trait_evidence": [], "recurring_loops": [], "biased_summary": "",
        "new_anticipations": [], "new_questions": [], "current_emotions": [],
    }
    if inner_result:
        r = inner_result if isinstance(inner_result, dict) else inner_result.model_dump()
        for k in ("continuity_note", "biased_summary", "recurring_loops",
                  "new_wants", "new_tensions", "new_anticipations", "new_questions",
                  "current_emotions"):
            if r.get(k):
                merged[k] = r[k]
    if signal_result:
        r = signal_result if isinstance(signal_result, dict) else signal_result.model_dump()
        for k in ("new_interests", "new_goals", "goal_progress_updates",
                  "new_world_beliefs", "trait_evidence"):
            if r.get(k):
                merged[k] = r[k]

    output = ReflectOutput(**merged)
    counts = await _apply_output(output)

    last_ts = kv_get(LAST_REFLECT_KEY) or "1970-01-01T00:00:00+00:00"
    moments_added = _harvest_person_moments(conn, since=last_ts)
    if moments_added:
        log.info("reflect_moments_harvested", count=moments_added)

    _decay_old_anticipations(conn)

    kv_set(LAST_REFLECT_KEY, _now().isoformat())
    log.info("reflect_complete", **counts, note=output.continuity_note[:80])
    return {"applied": counts, "continuity_note": output.continuity_note}


async def reflect_loop():
    """Background coroutine — checks every 5 minutes whether reflect is due."""
    import asyncio
    consecutive_failures = 0
    _CIRCUIT_THRESHOLD = 3
    while True:
        try:
            if consecutive_failures >= _CIRCUIT_THRESHOLD:
                log.warning("reflect_circuit_open", consecutive_failures=consecutive_failures)
                consecutive_failures = 0
            else:
                result = await run_reflect()
                if result is None:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
        except Exception as exc:
            log.warning("reflect_loop_error", error=str(exc))
            consecutive_failures += 1
        await asyncio.sleep(300)

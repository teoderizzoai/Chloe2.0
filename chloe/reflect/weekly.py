"""Weekly reflect — orchestrates procedural distillation + weekly self-modeling.

Runs Sunday ~03:00 local. Wraps two existing capable pieces:
- memory.procedural.distill_procedural — Flash, last 7 days of feedback pairs
- identity.self_model.run_weekly_self_model — Pro thinking, ~$0.08/week

Also runs (Chloe 3.0):
- trait adjudication (Flash) — review evidence log, apply weight updates
- narrative weaver (Opus) — produces NarrativeEntry + optional chapter transition
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from chloe.observability.logging import get_logger

log = get_logger("reflect.weekly")


async def run_weekly() -> dict:
    log.info("weekly_start")
    out: dict = {}

    try:
        from chloe.memory.procedural import distill_procedural
        new_ids = await distill_procedural()
        out["procedural"] = {"new_rules": len(new_ids), "ids": new_ids}
    except Exception as exc:
        log.warning("weekly_procedural_error", error=str(exc))
        out["procedural"] = {"error": str(exc)}

    # Trait adjudication — must run before Opus self-model so it has fresh data
    try:
        out["trait_adjudication"] = await run_trait_adjudication()
    except Exception as exc:
        log.warning("weekly_trait_adjudication_error", error=str(exc))
        out["trait_adjudication"] = {"error": str(exc)}

    try:
        from chloe.identity.self_model import run_weekly_self_model
        result = await run_weekly_self_model()
        out["self_model"] = result or {"error": "no_result"}
    except Exception as exc:
        log.warning("weekly_self_model_error", error=str(exc))
        out["self_model"] = {"error": str(exc)}

    # Narrative Consolidation — compress the week's witness entries before weaving
    try:
        out["narrative_consolidation"] = await run_narrative_consolidation()
    except Exception as exc:
        log.warning("weekly_narrative_consolidation_error", error=str(exc))
        out["narrative_consolidation"] = {"error": str(exc)}

    # Narrative Weaver — Opus call, runs after traits + beliefs are current
    try:
        out["narrative"] = await run_narrative_weaver()
    except Exception as exc:
        log.warning("weekly_narrative_error", error=str(exc))
        out["narrative"] = {"error": str(exc)}

    # Signal extraction — read narrative, derive structured updates
    try:
        out["signal_extraction"] = await run_signal_extraction()
    except Exception as exc:
        log.warning("weekly_signal_extraction_error", error=str(exc))
        out["signal_extraction"] = {"error": str(exc)}

    # Teo read synthesis — standing characterization of how Chloe reads Teo
    try:
        out["teo_read"] = await run_teo_read_synthesis()
    except Exception as exc:
        log.warning("weekly_teo_read_error", error=str(exc))
        out["teo_read"] = {"error": str(exc)}

    log.info("weekly_complete")
    return out


async def run_trait_adjudication() -> dict:
    """Flash pass: review week's trait evidence log and apply weight updates."""
    from chloe.state.db import get_connection
    from chloe.llm.gemini import GeminiClient
    from chloe.llm.schemas import TraitAdjudicationOutput
    from chloe.identity.trait_model import apply_stale_decay, _parse_json_list

    conn = get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # Build evidence log from identity_traits evidence_json fields (recent entries)
    trait_rows = conn.execute(
        "SELECT name, weight, gen_level, status, evidence_json, contradictions_json, windows_observed "
        "FROM identity_traits WHERE status NOT IN ('archived') ORDER BY weight DESC"
    ).fetchall()

    existing_traits_text = "\n".join(
        f"- {r['name']} (weight={r['weight']:.2f}, gen={r['gen_level']}, windows={r['windows_observed']}, status={r['status']})"
        for r in trait_rows
    ) or "(none yet)"

    evidence_lines = []
    for r in trait_rows:
        evidence = _parse_json_list(r["evidence_json"])
        recent = [e for e in evidence if (e.get("at") or "") >= cutoff]
        for e in recent[:5]:
            evidence_lines.append(f"[{r['name']}] REINFORCED: {e.get('behavior_observed', '')[:200]}")
        contradictions = _parse_json_list(r["contradictions_json"])
        recent_c = [e for e in contradictions if (e.get("at") or "") >= cutoff]
        for e in recent_c[:3]:
            evidence_lines.append(f"[{r['name']}] CONTRADICTED: {e.get('behavior_observed', '')[:200]}")

    if not evidence_lines:
        log.info("trait_adjudication_skipped", reason="no_recent_evidence")
        return {"skipped": True}

    client = GeminiClient()
    result = await client.flash(
        "trait_adjudication.md",
        {
            "existing_traits": existing_traits_text,
            "evidence_log": "\n".join(evidence_lines[:60]),
        },
        TraitAdjudicationOutput,
    )

    if not result:
        return {"error": "llm_returned_none"}

    output = TraitAdjudicationOutput(**result) if isinstance(result, dict) else result

    # Apply weight updates
    now_ts = datetime.now(timezone.utc).isoformat()
    for wu in output.weight_updates:
        try:
            row = conn.execute("SELECT weight FROM identity_traits WHERE name=?", (wu.name,)).fetchone()
            if row:
                new_w = max(0.0, min(1.0, float(row["weight"]) + wu.delta))
                conn.execute("UPDATE identity_traits SET weight=?, updated_at=? WHERE name=?",
                             (new_w, now_ts, wu.name))
        except Exception as exc:
            log.warning("trait_weight_update_failed", name=wu.name, error=str(exc))
    conn.commit()

    # Stale decay for decay_candidates
    for name in output.decay_candidates:
        try:
            row = conn.execute("SELECT weight FROM identity_traits WHERE name=?", (name,)).fetchone()
            if row:
                new_w = max(0.0, float(row["weight"]) - 0.02)
                conn.execute("UPDATE identity_traits SET weight=?, updated_at=? WHERE name=?",
                             (new_w, now_ts, name))
        except Exception as exc:
            log.warning("trait_decay_failed", name=name, error=str(exc))
    conn.commit()

    apply_stale_decay()

    # Temporal self-observation: compare current trait weights against 4 weeks ago
    try:
        _run_temporal_self_observation(conn, trait_rows, now_ts)
    except Exception as exc:
        log.warning("temporal_self_observation_failed", error=str(exc))

    log.info(
        "trait_adjudication_done",
        reinforced=len(output.reinforced),
        contradicted=len(output.contradicted),
        weight_updates=len(output.weight_updates),
        new_patterns=len(output.new_patterns),
    )
    return {
        "reinforced": output.reinforced,
        "contradicted": output.contradicted,
        "weight_updates": len(output.weight_updates),
        "new_patterns": len(output.new_patterns),
        "notes": output.notes,
    }


async def run_narrative_weaver() -> dict:
    """Opus call: produce a NarrativeEntry covering the past week."""
    from chloe.identity.narrative_weaver import weave_narrative
    return await weave_narrative()


async def run_narrative_consolidation() -> dict:
    """Consolidate the week's witness entries into one richer paragraph.

    Reads up to 25 unarchived witness entries, asks Flash to integrate them,
    stores the result as a 'consolidation' entry, and archives the originals.
    If fewer than 5 entries exist, skips.
    """
    from chloe.memory.narrative_store import collect_for_consolidation, add_entry, archive_entries
    from chloe.llm.gemini import GeminiClient
    from chloe.llm.schemas import WitnessOutput

    ids, texts = collect_for_consolidation(window=25)
    if not ids:
        log.info("narrative_consolidation_skipped", reason="too_few_entries")
        return {"skipped": True}

    combined = "\n\n".join(texts)
    prompt_context = {
        "exchange": (
            f"Here are {len(texts)} observations Chloe wrote about her recent conversations:\n\n"
            f"{combined}\n\n"
            "Write one or two integrated paragraphs in Chloe's voice that capture the real "
            "texture of what she's been noticing across all of these. Don't list — integrate. "
            "Preserve the contradictions and uncertainties. Write like she's thinking to herself."
        )
    }
    client = GeminiClient()
    result = await client.flash("witness.md", prompt_context, WitnessOutput)

    if not result:
        return {"error": "llm_returned_none"}

    observation = (result.get("observation") if isinstance(result, dict)
                   else getattr(result, "observation", "")) or ""
    observation = observation.strip()
    if not observation:
        return {"error": "empty_output"}

    consolidated_id = add_entry(observation, source="consolidation", salience=0.8)
    archive_entries(ids)
    log.info("narrative_consolidation_done", consolidated_id=consolidated_id,
             archived=len(ids), chars=len(observation))
    return {"consolidated_id": consolidated_id, "archived": len(ids)}


def _run_temporal_self_observation(conn, trait_rows, now_ts: str) -> None:
    """Compare current trait weights against 4-week-ago snapshot.

    When a dimension has shifted significantly (>0.1), generates a first-person
    temporal observation and stores it as an inner_belief.
    """
    from chloe.state.kv import get as kv_get, set as kv_set
    import json as _json

    current_week = datetime.now(timezone.utc).strftime("%Y-%W")
    four_weeks_ago_week = (datetime.now(timezone.utc) - timedelta(weeks=4)).strftime("%Y-%W")

    # Save current snapshot
    current_snapshot = {r["name"]: float(r["weight"]) for r in trait_rows}
    kv_set(f"trait_snapshot:{current_week}", current_snapshot)

    # Compare against 4-week-ago snapshot
    old_snapshot = kv_get(f"trait_snapshot:{four_weeks_ago_week}")
    if not old_snapshot or not isinstance(old_snapshot, dict):
        return

    shifts = []
    for name, cur_w in current_snapshot.items():
        old_w = float(old_snapshot.get(name, cur_w))
        delta = cur_w - old_w
        if abs(delta) > 0.1:
            shifts.append((name, "more" if delta > 0 else "less", abs(delta)))

    if not shifts:
        return

    # Pick the most significant shift and generate a first-person observation
    shifts.sort(key=lambda x: x[2], reverse=True)
    name, direction, magnitude = shifts[0]
    # Simple template — could be LLM-generated for richer phrasing
    observation = (
        f"I've been {direction} {name.replace('_', ' ')} lately — "
        f"I notice it especially when something matters."
    )

    conn.execute(
        "INSERT INTO inner_beliefs (text, confidence, tags, created_at) VALUES (?, ?, ?, ?)",
        (observation, 0.45, _json.dumps(["temporal_self_observation", "autobiographical"]), now_ts),
    )
    conn.commit()
    log.info("temporal_self_observation_written", trait=name, direction=direction)


async def run_teo_read_synthesis() -> dict:
    """Flash call: synthesize a standing read on Teo from recent witness entries.

    Reads recent witness/consolidation entries and generates a paragraph about
    how Chloe reads Teo — his patterns, tells, what she finds endearing or
    frustrating. Stored in kv as 'identity:teo_read'.
    """
    from chloe.memory.narrative_store import get_recent
    from chloe.llm.gemini import GeminiClient
    from chloe.llm.schemas import WitnessOutput
    from chloe.state.kv import set as kv_set

    entries = get_recent(n=15)
    using_onboarding = False

    # Fall back to onboarding memories if no narrative entries yet
    if not entries:
        from chloe.state.db import get_connection as _get_conn
        _conn = _get_conn()
        ob_rows = _conn.execute(
            "SELECT text FROM memories WHERE source='onboarding' ORDER BY id ASC"
        ).fetchall()
        if ob_rows:
            entries = [r["text"] for r in ob_rows]
            using_onboarding = True
            log.info("teo_read_synthesis_using_onboarding", count=len(entries))
        else:
            log.info("teo_read_synthesis_skipped", reason="no_narrative_entries")
            return {"skipped": True}

    client = GeminiClient()
    combined = "\n\n".join(entries)

    if using_onboarding:
        # Free-text call — onboarding data doesn't fit the witness.md JSON schema
        prompt = (
            f"You are Chloe. Teo shared the following about himself:\n\n{combined}\n\n"
            "Write one short paragraph in your voice about how you read him. "
            "Not a summary of facts — your felt sense of him: his patterns, his tells, "
            "what gives him away, what you find endearing or interesting in very specific ways. "
            "Write like you're thinking to yourself. Two to four sentences. Be concrete."
        )
        observation = await client.flash_text(prompt)
        observation = (observation or "").strip()
    else:
        prompt_context = {
            "exchange": (
                f"Here are Chloe's recent observations about her conversations:\n\n{combined}\n\n"
                "Based only on what appears here — not what you imagine — write one short paragraph "
                "in Chloe's voice about how she reads Teo. Not a summary of events. "
                "Her felt read on him: his patterns, his tells, what gives him away, "
                "what she finds endearing or frustrating in very particular ways. "
                "The thing about him is... Write like she's thinking to herself. "
                "If there isn't enough signal yet, return empty."
            )
        }
        result = await client.flash("witness.md", prompt_context, WitnessOutput)
        if not result:
            return {"error": "llm_returned_none"}
        observation = (result.get("observation") if isinstance(result, dict)
                       else getattr(result, "observation", "")) or ""
        observation = observation.strip()

    if not observation:
        return {"skipped": True, "reason": "empty_output"}

    kv_set("identity:teo_read", observation)
    log.info("teo_read_synthesis_done", chars=len(observation))
    return {"chars": len(observation)}


async def run_signal_extraction() -> dict:
    """Flash pass: read recent narrative entries, derive structured state updates.

    This is the only place narrative becomes typed fields. Runs weekly after
    the weaver so the narrative is current. Tables updated here are downstream
    of the narrative, not the primary source.
    """
    from chloe.memory.narrative_store import get_recent
    from chloe.llm.gemini import GeminiClient
    from chloe.llm.schemas import SignalBatch
    from chloe.state.db import get_connection

    entries = get_recent(n=10)
    if not entries:
        log.info("signal_extraction_skipped", reason="no_narrative_entries")
        return {"skipped": True}

    conn = get_connection()
    interests_rows = conn.execute(
        "SELECT id, label, intensity, gen_level FROM interest_garden WHERE intensity > 0 ORDER BY intensity DESC LIMIT 10"
    ).fetchall()
    interests_text = "\n".join(
        f"- id={r['id']} {r['label']} ({r['intensity']:.2f}, gen={r['gen_level']})"
        for r in interests_rows
    ) or "(none)"

    beliefs_rows = conn.execute(
        "SELECT topic, belief, confidence FROM world_beliefs ORDER BY confidence DESC LIMIT 10"
    ).fetchall()
    beliefs_text = "\n".join(
        f"- [{r['confidence']:.2f}] {r['topic']}: {r['belief']}" for r in beliefs_rows
    ) or "(none yet)"

    client = GeminiClient()
    result = await client.flash(
        "extract_signals.md",
        {
            "narrative_entries": "\n\n---\n\n".join(entries),
            "interests": interests_text,
            "world_beliefs": beliefs_text,
        },
        SignalBatch,
    )

    if not result:
        return {"error": "llm_returned_none"}

    batch = SignalBatch(**result) if isinstance(result, dict) else result
    applied: dict = {"beliefs": 0, "promotions": 0, "tensions": 0}

    for b in batch.belief_updates:
        try:
            from chloe.inner.belief_revision import store_new_belief
            import asyncio
            asyncio.create_task(store_new_belief(
                topic=b.topic.strip()[:80],
                belief=b.belief.strip(),
                proposed_confidence=b.confidence,
                noticing=b.noticing,
            ))
            applied["beliefs"] += 1
        except Exception as exc:
            log.warning("signal_belief_failed", error=str(exc))

    for p in batch.interest_promotions:
        try:
            from chloe.identity.interest_garden import promote_interest
            promote_interest(p.interest_id, p.new_level, evidence=p.reason)
            applied["promotions"] += 1
        except Exception as exc:
            log.warning("signal_promotion_failed", error=str(exc))

    for t in batch.new_tensions:
        try:
            from chloe.inner.pressure import add_tension
            add_tension(t.strip(), tags=["narrative"], pressure=0.5)
            applied["tensions"] += 1
        except Exception as exc:
            log.warning("signal_tension_failed", error=str(exc))

    log.info("signal_extraction_done", **applied, notes=batch.notes[:80] if batch.notes else "")
    return {"applied": applied, "notes": batch.notes}

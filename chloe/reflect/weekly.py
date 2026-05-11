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

    # Narrative Weaver — Opus call, runs after traits + beliefs are current
    try:
        out["narrative"] = await run_narrative_weaver()
    except Exception as exc:
        log.warning("weekly_narrative_error", error=str(exc))
        out["narrative"] = {"error": str(exc)}

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

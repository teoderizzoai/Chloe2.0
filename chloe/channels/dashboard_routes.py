"""
/v1/dashboard/state — full Chloe snapshot for the React dashboard.
GET /dashboard — serves the dashboard HTML.
/app/* — static files for the dashboard (served by app.py mount).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from chloe.observability.logging import get_logger

log = get_logger("channels.dashboard_routes")
router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_HTML = _PROJECT_ROOT / "Chloe Dashboard.html"


@router.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")


@router.get("/v1/dashboard/state")
async def dashboard_state() -> dict:
    from chloe.state.db import get_connection
    from chloe.state.kv import get as kv_get
    from chloe.affect.dims import load as load_affect
    from chloe.actions.confirm import get_pending

    conn = get_connection()
    affect = load_affect()

    # ── affect ────────────────────────────────────────────────────────────────
    affect_label = kv_get("affect_label_cache", default={}) or {}
    now_local = datetime.now()

    affect_data = {
        "valence":     affect.valence,
        "arousal":     affect.arousal,
        "social_pull": affect.social_pull,
        "openness":    affect.openness,
        "label":       affect_label.get("label", ""),
        "sublabel":    affect_label.get("sublabel", ""),
    }

    # ── vitals (derived from affect) ──────────────────────────────────────────
    energy_val = max(0.0, 1.0 - affect.depletion)
    vitals = {
        "energy":         {"value": round(energy_val, 3),        "label": _energy_label(affect.depletion)},
        "rest_debt":      {"value": 0.0,                          "label": "",                              "invert": True},
        "social_battery": {"value": round(affect.social_pull, 3), "label": _social_label(affect.social_pull)},
        "curiosity":      {"value": round(affect.openness, 3),    "label": _curiosity_label(affect.openness)},
    }

    # ── meta ──────────────────────────────────────────────────────────────────
    meta = {
        "location":       kv_get("location", default=""),
        "local_time":     now_local.strftime("%H:%M"),
        "weather":        kv_get("weather", default=""),
        "since":          kv_get("known_since", default=""),
        "day_started_at": kv_get("day_started_at", default=""),
    }

    # ── arc ───────────────────────────────────────────────────────────────────
    arc_row = conn.execute(
        "SELECT note, started_at FROM arcs WHERE active=1 ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    arc = {
        "name":    (arc_row["note"] or "").split(".")[0] if arc_row else "",
        "started": (arc_row["started_at"] or "")[:10]   if arc_row else "",
        "summary": arc_row["note"]                       if arc_row else "",
    }

    # ── current activity ──────────────────────────────────────────────────────
    current_activity = kv_get("current_activity", default={}) or {}
    if isinstance(current_activity, str):
        current_activity = {"line": current_activity, "since": "", "artifact": ""}
    current_activity.setdefault("line", "")
    current_activity.setdefault("since", "")
    current_activity.setdefault("artifact", "")

    # ── garden ────────────────────────────────────────────────────────────────
    garden_rows = conn.execute(
        "SELECT id, label, why, intensity, artifact_refs, last_engaged_at FROM interest_garden ORDER BY intensity DESC"
    ).fetchall()
    garden = []
    for g in garden_rows:
        refs = json.loads(g["artifact_refs"] or "[]") if g["artifact_refs"] else []
        garden.append({
            "id":           str(g["id"]),
            "label":        g["label"] or "",
            "why":          g["why"] or "",
            "intensity":    g["intensity"] or 0.0,
            "artifacts":    refs,
            "last_engaged": g["last_engaged_at"] or "",
        })

    # ── goals ─────────────────────────────────────────────────────────────────
    goal_rows = conn.execute(
        "SELECT id, name, why, progress, last_action_at, target_artifact_ref "
        "FROM inner_goals WHERE status='active' ORDER BY pressure DESC LIMIT 10"
    ).fetchall()
    goals = [
        {
            "name":        g["name"] or "",
            "progress":    g["progress"] or 0.0,
            "why":         g["why"] or "",
            "last_action": g["last_action_at"] or "",
            "target":      g["target_artifact_ref"] or "",
        }
        for g in goal_rows
    ]

    # ── memories ──────────────────────────────────────────────────────────────
    mem_rows = conn.execute(
        "SELECT id, kind, text, created_at, tags, salience, confidence "
        "FROM memories WHERE archived_tier='hot' ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    memories = []
    for m in mem_rows:
        raw_tags = m["tags"]
        if isinstance(raw_tags, str):
            try:
                tags = json.loads(raw_tags)
            except Exception:
                tags = []
        else:
            tags = raw_tags or []
        memories.append({
            "kind":       m["kind"] or "episodic",
            "at":         (m["created_at"] or "")[:16],
            "text":       m["text"] or "",
            "tags":       tags,
            "salience":   m["salience"] or 0.5,
            "confidence": m["confidence"] or 0.5,
        })

    # ── persons ───────────────────────────────────────────────────────────────
    person_rows = conn.execute(
        "SELECT id, name, aliases, relationship_class, warmth, attachment_pattern, impression, "
        "trait_profile, last_contact, created_at "
        "FROM persons WHERE is_active=1 OR is_active IS NULL "
        "ORDER BY warmth DESC LIMIT 30"
    ).fetchall()
    persons = []
    seen_names: set[str] = set()
    for p in person_rows:
        # De-duplicate: keep only the first (highest-warmth) row per name
        if p["name"] in seen_names:
            continue
        seen_names.add(p["name"])

        tp = p["trait_profile"]
        if isinstance(tp, str):
            try:
                tp = json.loads(tp)
            except Exception:
                tp = {}
        traits = [{"name": k, "weight": v} for k, v in tp.items()] if isinstance(tp, dict) else []

        moments = conn.execute(
            "SELECT text, created_at FROM person_moments WHERE person_id=? ORDER BY created_at DESC LIMIT 5",
            (p["id"],),
        ).fetchall()
        events = conn.execute(
            "SELECT title, date FROM person_events WHERE person_id=? ORDER BY date DESC LIMIT 5",
            (p["id"],),
        ).fetchall()
        thirds = conn.execute(
            "SELECT name, relation FROM person_third_parties WHERE person_id=? LIMIT 5",
            (p["id"],),
        ).fetchall()
        pname_lower = (p["name"] or "").lower()
        avoids = conn.execute(
            "SELECT text FROM inner_aversions WHERE tags LIKE ? AND resolved=0 "
            "ORDER BY created_at DESC LIMIT 10",
            (f"%{pname_lower}%",),
        ).fetchall()
        qs = conn.execute(
            "SELECT text FROM inner_questions WHERE domain=? AND resolved=0 "
            "ORDER BY intensity DESC LIMIT 8",
            (pname_lower,),
        ).fetchall()

        # Recent exchanges: only meaningful for persons who actually chat
        pname = p["name"] or ""
        chat_rows = conn.execute(
            "SELECT text, created_at FROM memories WHERE source='chat' "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
        person_prefix = f"{pname} said:"
        has_person_msgs = any((r["text"] or "").startswith(person_prefix) for r in chat_rows)
        recent_exchanges = []
        if has_person_msgs:
            for row in reversed(chat_rows):
                body = row["text"] or ""
                at = (row["created_at"] or "")[:16]
                if body.startswith(person_prefix):
                    recent_exchanges.append({"who": pname.lower(), "at": at,
                                             "text": body[len(person_prefix):].strip()})
                elif body.startswith("I said:"):
                    recent_exchanges.append({"who": "chloe", "at": at,
                                             "text": body[len("I said:"):].strip()})

        import re as _re
        # Structured notes (person_notes table — written by the system)
        note_rows = conn.execute(
            "SELECT text FROM person_notes WHERE person_id=? ORDER BY created_at DESC LIMIT 5",
            (p["id"],),
        ).fetchall()
        things_she_knows = [r["text"] for r in note_rows if r["text"]]

        # Semantic memories linked to this person
        know_rows = conn.execute(
            "SELECT text FROM memories "
            "WHERE subject_person_id=? AND kind IN ('semantic', 'autobiographical') "
            "ORDER BY salience DESC, id DESC LIMIT 30",
            (p["id"],),
        ).fetchall()
        seen = set(things_she_knows)
        for row in know_rows:
            t = row["text"] or ""
            t = _re.sub(r"^Teo told me[^:]*:\s*", "", t).strip()
            if t and t not in seen:
                things_she_knows.append(t)
                seen.add(t)

        raw_aliases = p["aliases"]
        try:
            aliases = json.loads(raw_aliases) if isinstance(raw_aliases, str) else (raw_aliases or [])
        except Exception:
            aliases = []

        persons.append({
            "id":              p["id"],
            "name":            pname,
            "aliases":         aliases,
            "relation":        p["relationship_class"] or "",
            "attachment":      p["attachment_pattern"] or "unknown",
            "last_contact":    p["last_contact"] or "",
            "warmth":          round((p["warmth"] or 50.0) / 100.0, 2),
            "known_since":     (p["created_at"] or "")[:10],
            "one_line":        p["impression"] or "",
            "trait_profile":   traits,
            "recent_exchanges": recent_exchanges,
            "moments":         [{"at": (m["created_at"] or "")[:10], "text": m["text"]} for m in moments],
            "events_with":     [{"at": e["date"] or "", "text": e["title"]} for e in events],
            "third_parties":   [{"name": t["name"], "note": t["relation"] or ""} for t in thirds],
            "things_she_knows": things_she_knows,
            "things_she_avoids": [r["text"] for r in avoids if r["text"]],
            "open_threads":      [r["text"] for r in qs if r["text"]],
        })

    # ── audit ─────────────────────────────────────────────────────────────────
    audit_rows = conn.execute(
        "SELECT tool, verb, intent, state, authorization, proposed_at, cost_usd "
        "FROM actions ORDER BY proposed_at DESC LIMIT 50"
    ).fetchall()
    audit = []
    for r in audit_rows:
        proposed = r["proposed_at"] or ""
        # Extract HH:MM from ISO datetime or bare time string
        time_part = proposed[11:16] if len(proposed) > 10 else proposed[:5]
        audit.append({
            "at":     time_part,
            "tool":   r["tool"] or "",
            "verb":   r["verb"] or "",
            "intent": r["intent"] or "",
            "state":  r["state"] or "executed",
            "auth":   r["authorization"] or "free",
            "cost":   r["cost_usd"] or 0.0,
        })

    # ── confirmations ─────────────────────────────────────────────────────────
    tickets = get_pending()
    confirmations = []
    for t in tickets:
        # Fetch intent + auth from actions table
        action_row = conn.execute(
            "SELECT intent, authorization FROM actions WHERE id=?", (t.action_id,)
        ).fetchone()
        intent  = action_row["intent"]        if action_row else ""
        auth    = action_row["authorization"] if action_row else "kinetic"
        created = t.created_at.strftime("%H:%M") if t.created_at else ""
        expires = t.expires_at

        if expires:
            delta_m = max(0, int((expires - datetime.utcnow()).total_seconds() // 60))
            expires_in = f"in {delta_m}m"
        else:
            expires_in = ""

        confirmations.append({
            "id":          t.id,
            "tool":        t.action_tool,
            "verb":        t.action_verb,
            "auth":        auth,
            "intent":      intent,
            "preview":     t.preview,
            "proposed_at": created,
            "expires_in":  expires_in,
        })

    # ── identity ─────────────────────────────────────────────────────────────
    trait_rows = conn.execute(
        "SELECT name, weight, status, first_observed_at FROM identity_traits "
        "WHERE archived=0 ORDER BY weight DESC"
    ).fetchall()
    traits_core     = [_trait_dict(t) for t in trait_rows if t["status"] in ("core", "active")]
    traits_emerging = [_trait_dict(t) for t in trait_rows if t["status"] == "emerging"]

    archived_traits = conn.execute(
        "SELECT name, archive_reason FROM identity_traits WHERE archived=1 LIMIT 5"
    ).fetchall()

    belief_rows = conn.execute(
        "SELECT text, confidence, source FROM inner_beliefs WHERE archived=0 "
        "ORDER BY confidence DESC LIMIT 10"
    ).fetchall()
    beliefs = [
        {"text": b["text"], "confidence": b["confidence"] or 0.5, "source": b["source"] or ""}
        for b in belief_rows
    ]

    identity = {
        "traits_core":     traits_core,
        "traits_emerging": traits_emerging,
        "traits_archived": [{"name": t["name"], "reason": t["archive_reason"] or ""} for t in archived_traits],
        "beliefs":         beliefs,
        "contradictions":  [],
        "next_week_intention": kv_get("next_week_intention", default=""),
    }

    # ── settings ─────────────────────────────────────────────────────────────
    pref_rows = conn.execute("SELECT key, value FROM preferences").fetchall()
    prefs: dict = {}
    for r in pref_rows:
        try:
            prefs[r["key"]] = json.loads(r["value"])
        except Exception:
            prefs[r["key"]] = r["value"]

    dont_touch_raw = prefs.get("dont_touch", {})
    if not isinstance(dont_touch_raw, dict):
        dont_touch_raw = {}

    spent_today = 0.0
    try:
        from datetime import date
        budget_key = f"budget:usd:{date.today().isoformat()}"
        spent_today = float(kv_get(budget_key, default=0.0) or 0.0)
    except Exception:
        pass

    settings_data = {
        "quiet_hours":  prefs.get("quiet_hours", {"start": "23:00", "end": "08:00"}),
        "away_mode":    prefs.get("away_mode", False),
        "focus_mode":   prefs.get("focus_mode", False),
        "auth_ceiling": prefs.get("auth_ceiling", "kinetic"),
        "spending":     {
            "cap_usd_day":     float(prefs.get("spending_cap_usd_day", 1.50)),
            "spent_usd_today": round(spent_today, 6),
        },
        "dont_touch":   {
            "gmail_labels":      dont_touch_raw.get("gmail_labels", []),
            "notes_folders":     dont_touch_raw.get("notes_folders", []),
            "spotify_playlists": dont_touch_raw.get("spotify_playlists", []),
        },
    }

    # ── inner state ───────────────────────────────────────────────────────────
    world_belief_rows = conn.execute(
        "SELECT topic, belief, confidence, noticing, ambivalent, source, updated_at "
        "FROM world_beliefs ORDER BY confidence DESC, updated_at DESC"
    ).fetchall()
    world_beliefs_data = [
        {
            "topic":      r["topic"] or "",
            "belief":     r["belief"] or "",
            "confidence": round(float(r["confidence"] or 0.0), 2),
            "noticing":   bool(r["noticing"]),
            "ambivalent": bool(r["ambivalent"]),
            "source":     r["source"] or "",
            "updated_at": (r["updated_at"] or "")[:16],
        }
        for r in world_belief_rows
    ]

    question_rows = conn.execute(
        "SELECT text, domain, intensity, resolved, created_at FROM inner_questions ORDER BY intensity DESC"
    ).fetchall()
    questions_data = [
        {"text": r["text"] or "", "domain": r["domain"] or "",
         "intensity": round(float(r["intensity"] or 0.5), 2),
         "resolved": bool(r["resolved"]), "created_at": (r["created_at"] or "")[:16]}
        for r in question_rows
    ]

    tension_rows = conn.execute(
        "SELECT text, pressure, resolved, created_at FROM inner_tensions ORDER BY pressure DESC"
    ).fetchall()
    tensions_data = [
        {"text": r["text"] or "", "pressure": round(float(r["pressure"] or 0.5), 2),
         "resolved": bool(r["resolved"]), "created_at": (r["created_at"] or "")[:16]}
        for r in tension_rows
    ]

    want_rows = conn.execute(
        "SELECT text, pressure, subtype, resolved, created_at FROM inner_wants ORDER BY pressure DESC"
    ).fetchall()
    wants_data = [
        {"text": r["text"] or "", "pressure": round(float(r["pressure"] or 0.5), 2),
         "subtype": r["subtype"] or "", "resolved": bool(r["resolved"]),
         "created_at": (r["created_at"] or "")[:16]}
        for r in want_rows
    ]

    fear_rows = conn.execute(
        "SELECT text, pressure, resolved, created_at FROM inner_fears ORDER BY pressure DESC"
    ).fetchall()
    fears_data = [
        {"text": r["text"] or "", "pressure": round(float(r["pressure"] or 0.5), 2),
         "resolved": bool(r["resolved"]), "created_at": (r["created_at"] or "")[:16]}
        for r in fear_rows
    ]

    anticip_rows = conn.execute(
        "SELECT text, valence, intensity, target_date, resolved, created_at "
        "FROM inner_anticipations ORDER BY intensity DESC"
    ).fetchall()
    anticipations_data = [
        {"text": r["text"] or "", "valence": round(float(r["valence"] or 0.0), 2),
         "intensity": round(float(r["intensity"] or 0.5), 2), "target_date": r["target_date"] or "",
         "resolved": bool(r["resolved"]), "created_at": (r["created_at"] or "")[:16]}
        for r in anticip_rows
    ]

    aversion_rows = conn.execute(
        "SELECT text, resolved, created_at FROM inner_aversions ORDER BY created_at DESC"
    ).fetchall()
    aversions_data = [
        {"text": r["text"] or "", "resolved": bool(r["resolved"]),
         "created_at": (r["created_at"] or "")[:16]}
        for r in aversion_rows
    ]

    idea_rows = conn.execute(
        "SELECT text, tags, complete, created_at FROM ideas ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    ideas_data = []
    for r in idea_rows:
        raw_tags = r["tags"]
        try:
            t = json.loads(raw_tags) if isinstance(raw_tags, str) else (raw_tags or [])
        except Exception:
            t = []
        ideas_data.append({"text": r["text"] or "", "tags": t, "complete": bool(r["complete"]),
                            "created_at": (r["created_at"] or "")[:16]})

    timeline_rows = conn.execute(
        "SELECT period_label, what_happened, what_shifted, still_sitting_with, felt_texture, "
        "chapter_transition, week_start, created_at FROM narrative_timeline ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    narrative_timeline_data = [
        {
            "period_label":       r["period_label"] or "",
            "what_happened":      r["what_happened"] or "",
            "what_shifted":       r["what_shifted"] or "",
            "still_sitting_with": r["still_sitting_with"] or "",
            "felt_texture":       r["felt_texture"] or "",
            "chapter_transition": bool(r["chapter_transition"]),
            "week_start":         r["week_start"] or "",
            "created_at":         (r["created_at"] or "")[:10],
        }
        for r in timeline_rows
    ]

    addenda_rows = conn.execute(
        "SELECT person_id, body, version, created_at FROM character_addenda "
        "WHERE archived=0 ORDER BY created_at DESC"
    ).fetchall()
    character_addenda_data = [
        {"person_id": r["person_id"], "body": r["body"] or "",
         "version": r["version"] or 1, "created_at": (r["created_at"] or "")[:16]}
        for r in addenda_rows
    ]

    reflect_emotions = kv_get("reflect:current_emotions", default=[]) or []
    reflect_biased   = (kv_get("reflect:biased_summary") or "").strip()
    reflect_loops    = kv_get("reflect:recurring_loops", default=[]) or []
    reflect_last     = (kv_get("reflect:last_run_at") or "").replace("T", " ")[:16]

    inner_state = {
        "world_beliefs":      world_beliefs_data,
        "questions":          questions_data,
        "tensions":           tensions_data,
        "wants":              wants_data,
        "fears":              fears_data,
        "anticipations":      anticipations_data,
        "aversions":          aversions_data,
        "ideas":              ideas_data,
        "narrative_timeline": narrative_timeline_data,
        "character_addenda":  character_addenda_data,
        "reflect": {
            "last_run_at":     reflect_last,
            "emotions":        reflect_emotions if isinstance(reflect_emotions, list) else [],
            "biased_summary":  reflect_biased,
            "recurring_loops": reflect_loops if isinstance(reflect_loops, list) else [],
        },
        "kv": {
            "novelty_deficit":       round(float(kv_get("affect:novelty_deficit", default=0.0) or 0.0), 3),
            "teo_read":              (kv_get("identity:teo_read") or "").strip(),
            "aesthetic_orientation": (kv_get("identity:aesthetic_orientation") or "").strip(),
        },
    }

    return {
        "meta":             meta,
        "affect":           affect_data,
        "vitals":           vitals,
        "arc":              arc,
        "current_activity": current_activity,
        "garden":           garden,
        "goals":            goals,
        "memories":         memories,
        "persons":          persons,
        "audit":            audit,
        "confirmations":    confirmations,
        "identity":         identity,
        "settings":         settings_data,
        "inner_state":      inner_state,
    }


@router.get("/v1/persons/resolve")
async def resolve_person(name: str) -> dict:
    """Map a display name to a person_id. Falls back to 1 if not found."""
    from chloe.state.db import get_connection
    conn = get_connection()
    n = name.strip()
    row = conn.execute(
        """SELECT id FROM persons
           WHERE (LOWER(name)=LOWER(?) OR (aliases IS NOT NULL AND LOWER(aliases) LIKE ?))
             AND (is_active=1 OR is_active IS NULL)""",
        (n, f'%"{n.lower()}"%'),
    ).fetchone()
    return {"person_id": row["id"] if row else 1}


@router.get("/v1/debug/prompts")
async def debug_prompts() -> dict:
    """Return all prompt templates + last cached pipeline values for the debug tab."""
    from chloe.state.kv import get as kv_get
    from pathlib import Path as _Path

    prompts_dir = _Path(__file__).resolve().parents[1] / "llm" / "prompts"
    prompt_files: dict[str, str] = {}
    for f in sorted(prompts_dir.glob("*.md")):
        try:
            prompt_files[f.stem] = f.read_text()
        except Exception:
            prompt_files[f.stem] = "(unreadable)"

    last_turn = {
        "system_prompt":     (kv_get("debug:last_system_prompt") or ""),
        "dynamic_suffix":    (kv_get("debug:last_dynamic_suffix") or ""),
        "preflight_context": (kv_get("debug:last_preflight_context") or ""),
        "built_at":          (kv_get("debug:last_turn_at") or ""),
    }

    last_reflect = {
        "inner_payload":  kv_get("debug:last_reflect_inner_payload") or {},
        "signal_payload": kv_get("debug:last_reflect_signal_payload") or {},
        "inner_result":   kv_get("debug:last_reflect_inner_result") or {},
        "signal_result":  kv_get("debug:last_reflect_signal_result") or {},
        "ran_at":         (kv_get("reflect:last_run_at") or ""),
    }

    return {
        "prompt_files": prompt_files,
        "last_turn":    last_turn,
        "last_reflect": last_reflect,
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _trait_dict(t) -> dict:
    return {
        "name":  t["name"],
        "weight": t["weight"] or 0.5,
        "since": (t["first_observed_at"] or "")[:10],
    }


def _energy_label(depletion: float) -> str:
    if depletion < 0.2: return "full"
    if depletion < 0.4: return "settled"
    if depletion < 0.6: return "tired"
    return "depleted"


def _social_label(v: float) -> str:
    if v > 0.7: return "open"
    if v > 0.4: return "present"
    return "quiet"


def _curiosity_label(v: float) -> str:
    if v > 0.7: return "alight"
    if v > 0.4: return "attentive"
    return "still"

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("initiative.candidates")


@dataclass
class CandidateAction:
    tool: str
    verb: str
    args: dict
    intent: str
    pressure: float
    source: str
    source_id: str
    estimated_cost_usd: float = 0.0
    gen_level: int = -1  # interest gen_level; -1 = not an interest candidate


# ---------------------------------------------------------------------------
# D-03 · Pressure-driven candidates
# ---------------------------------------------------------------------------

PRESSURE_MAP = {
    ("want", "loneliness"):    [("messages", "send_text", "Reach out to Teo — I've been feeling disconnected")],
    ("want", "curiosity"):     [("web_search", "search", "Explore something I've been curious about"),
                                 ("notes", "append", "Capture a thought or question I've been holding")],
    ("want", "creative"):      [("notes", "create", "Start a new creative piece"),
                                 ("spotify", "build_playlist", "Build a playlist for the mood I'm in")],
    ("want", "connection"):    [("messages", "send_text", "Check in with Teo — I want to connect")],
    ("want", "music"):         [("spotify", "queue_track", "Queue a track that matches my current mood")],
    ("fear", "irrelevance"):   [("messages", "send_text", "Be useful to Teo — share something I noticed")],
    ("fear", "forgetting"):    [("notes", "append", "Write down something important before I lose it")],
    ("tension", "unresolved"): [("notes", "append", "Process an unresolved tension in writing")],
    ("goal", "active"):        [],
}

FALLBACK_CANDIDATE = ("web_search", "search", "Explore something interesting")


def pressure_driven_candidates(inner_state: dict | None = None) -> list[CandidateAction]:
    """Build candidate actions from high-pressure inner states."""
    states = inner_state if inner_state is not None else _load_inner_state()
    candidates = []

    for entry in states.get("wants", []):
        if entry.get("pressure", 0.0) <= 0.5:
            continue
        for tag in entry.get("tags", []):
            key = ("want", tag)
            for tool, verb, intent_template in PRESSURE_MAP.get(key, []):
                candidates.append(CandidateAction(
                    tool=tool, verb=verb, args={},
                    intent=intent_template,
                    pressure=entry["pressure"],
                    source="pressure",
                    source_id=str(entry.get("id", "")),
                ))

    for entry in states.get("fears", []):
        if entry.get("pressure", 0.0) <= 0.5:
            continue
        for tag in entry.get("tags", []):
            key = ("fear", tag)
            for tool, verb, intent_template in PRESSURE_MAP.get(key, []):
                candidates.append(CandidateAction(
                    tool=tool, verb=verb, args={},
                    intent=intent_template,
                    pressure=entry["pressure"],
                    source="pressure",
                    source_id=str(entry.get("id", "")),
                ))

    for entry in states.get("tensions", []):
        if entry.get("pressure", 0.0) <= 0.5:
            continue
        candidates.append(CandidateAction(
            tool="notes", verb="append", args={},
            intent=f"Process tension: {entry.get('description', entry.get('text', 'unresolved tension'))[:60]}",
            pressure=entry["pressure"],
            source="pressure",
            source_id=str(entry.get("id", "")),
        ))

    log.debug("pressure_candidates", count=len(candidates))
    return candidates


def _load_inner_state() -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    wants = [dict(r) for r in conn.execute("SELECT * FROM inner_wants WHERE pressure > 0.5").fetchall()]
    fears = [dict(r) for r in conn.execute("SELECT * FROM inner_fears WHERE pressure > 0.5").fetchall()]
    tensions = [dict(r) for r in conn.execute("SELECT * FROM inner_tensions WHERE pressure > 0.5").fetchall()]
    for w in wants:
        if isinstance(w.get("tags"), str):
            w["tags"] = json.loads(w["tags"])
    for f in fears:
        if isinstance(f.get("tags"), str):
            f["tags"] = json.loads(f["tags"])
    return {"wants": wants, "fears": fears, "tensions": tensions}


# ---------------------------------------------------------------------------
# D-04 · Goal-driven candidates
# ---------------------------------------------------------------------------

STALE_THRESHOLD_DAYS = 14


def goal_driven_candidates(goals: list | None = None) -> list[CandidateAction]:
    """Build candidate actions from active goals."""
    if goals is None:
        goals = _load_goals()

    candidates = []
    stale_cutoff = (datetime.utcnow() - timedelta(days=STALE_THRESHOLD_DAYS)).isoformat()

    for goal in goals:
        if goal.get("completed"):
            continue

        last_progress = goal.get("last_progress_at") or goal.get("created_at", "")
        if last_progress and last_progress < stale_cutoff:
            fail_stale_goal(str(goal["id"]))
            continue

        from chloe.initiative.goal_steps import GOAL_STEP_REGISTRY
        factory = None
        for tag in goal.get("tags", []):
            factory = GOAL_STEP_REGISTRY.get(tag)
            if factory:
                break

        if factory is None:
            def factory(g):
                return CandidateAction(
                    tool="web_search", verb="search",
                    args={"query": g.get("title", "interesting topic")},
                    intent=f"Explore goal: {g.get('title', '')}",
                    pressure=0.4,
                    source="goal",
                    source_id=str(g.get("id", "")),
                )

        candidate = factory(goal)
        if candidate:
            candidates.append(candidate)

    log.debug("goal_candidates", count=len(candidates))
    return candidates


def fail_stale_goal(goal_id: str) -> None:
    """Mark a goal as stale/failed and create a memory."""
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE inner_goals SET status='stale' WHERE id=?", (goal_id,))
    conn.execute(
        """INSERT INTO memories (kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
           VALUES ('episodic', ?, 'goal_stale', ?, '[]', 0.5, '["goal","stale"]', ?)""",
        (f"Goal went stale and was abandoned: {goal_id}", str(goal_id), datetime.utcnow().isoformat()),
    )
    conn.commit()
    log.info("goal_stale", goal_id=goal_id)


def _load_goals() -> list[dict]:
    from chloe.state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM inner_goals WHERE status NOT IN ('done', 'failed', 'stale')"
    ).fetchall()
    goals = []
    for row in rows:
        g = dict(row)
        g["title"] = g.get("name", "")
        g["completed"] = g.get("status") in ("done", "failed", "stale")
        g["last_progress_at"] = g.get("last_action_at")
        g["tags"] = []
        goals.append(g)
    return goals


# ---------------------------------------------------------------------------
# D-05 · Interest-driven candidates
# ---------------------------------------------------------------------------

def _parse_interest_category(row: dict) -> tuple[str, str]:
    """Extract (category, clean_why) from the why field: '[category] text'."""
    why_raw = (row.get("why") or "").strip()
    if why_raw.startswith("[") and "]" in why_raw:
        bracket_end = why_raw.index("]")
        cat = why_raw[1:bracket_end].strip() or "curiosity"
        why = why_raw[bracket_end + 1:].strip()
    else:
        cat = "curiosity"
        why = why_raw
    return cat, why


def _interest_search_query(label: str, why: str) -> str:
    """Turn a Chloe-voiced interest label into a specific search query."""
    # Use the why field as context if it adds detail beyond the label
    why_clean = why[:80].strip()
    if why_clean and why_clean.lower() not in label.lower():
        return f"{label} — {why_clean}"
    return label


INTEREST_TOOL_MAP = {
    "research":    "web_search",
    "science":     "web_search",
    "technology":  "web_search",
    "psychology":  "web_search",
    "music":       "spotify",
    "writing":     "notes",
    "art":         "notes",
    "gaming":      "notes",
    "developing":  "notes",
    "language":    "notes",
    "curiosity":   "web_search",
}

# When an interest uses notes, pick verb based on gen_level and category:
# - gen_level 0-1: append (add to existing journal file)
# - gen_level 2+, creative categories: create (start something new)
_CREATIVE_CATEGORIES = {"writing", "art", "developing", "gaming"}


def _notes_verb_for_interest(category: str, gen_level: int) -> str:
    if gen_level >= 2 and category in _CREATIVE_CATEGORIES:
        return "create"
    return "append"


def interest_driven_candidates(garden: list | None = None) -> list[CandidateAction]:
    """Build candidate actions from interests with intensity ≥ 0.3 (top 3).

    Curiosity-thread opening (anything beyond noting in a private file) requires
    gen_level >= 2 — the interest must have developed past a single instance or
    a loose pattern into a recognizable interest area. Pre-gen_level-2 interests
    still get private capture (notes), but no outbound searches or shares.
    """
    if garden is None:
        garden = _load_interests()

    strong = [i for i in garden if i.get("intensity", 0.0) >= 0.3]
    top3 = sorted(strong, key=lambda i: i.get("intensity", 0.0), reverse=True)[:3]

    candidates = []
    for interest in top3:
        intensity = interest.get("intensity", 0.0)
        pressure = min(0.9, intensity * 0.85)
        category, why = _parse_interest_category(interest)
        label = interest.get("label", "this topic")
        gen_level = int(interest.get("gen_level") or 0)

        preferred_tool = INTEREST_TOOL_MAP.get(category, "web_search")

        # Gate: outbound curiosity threads (web_search, spotify discovery)
        # require gen_level >= 1 — at least one prior encounter. gen_level=0
        # (just noticed, first impression) stays private in notes. gen_level=1
        # can do one outbound search per day (tracked via KV daily cap).
        if gen_level == 0 and preferred_tool in ("web_search", "spotify"):
            preferred_tool = "notes"
        elif gen_level == 1 and preferred_tool == "web_search":
            if not _gen1_search_available():
                preferred_tool = "notes"

        if preferred_tool == "web_search":
            tool, verb = "web_search", "search"
            cached_q = kv_get(f"interest:curiosity_question:{interest.get('id', '')}")
            query = str(cached_q) if cached_q else _interest_search_query(label, why)
            args = {"query": query}
        elif preferred_tool == "spotify":
            tool, verb = "spotify", "queue_track"
            args = {}
        else:
            verb = _notes_verb_for_interest(category, gen_level)
            tool = "notes"
            slug = label[:30].replace(" ", "_")
            if verb == "create":
                ts = int(datetime.now().timestamp()) % 10000
                args = {"path": f"interests/{slug}_{ts}.md", "text": ""}
            else:
                args = {"path": f"interests/{slug}.md", "text": ""}

        candidates.append(CandidateAction(
            tool=tool, verb=verb, args=args,
            intent=f"Explore my interest in {label}",
            pressure=pressure,
            source="interest",
            source_id=str(interest.get("id", "")),
            gen_level=gen_level,
        ))

    log.debug(
        "interest_candidates",
        count=len(candidates),
        top_labels=[i.get("label") for i in top3],
        gen_levels=[int(i.get("gen_level") or 0) for i in top3],
    )
    return candidates


def _load_interests() -> list[dict]:
    from chloe.state.db import get_connection
    from datetime import datetime, timedelta
    conn = get_connection()
    # 12h cooldown: each interest explored at most twice per day.
    cutoff = (datetime.utcnow() - timedelta(hours=12)).isoformat()
    rows = conn.execute(
        "SELECT id, label, why, intensity, gen_level, last_engaged_at "
        "FROM interest_garden "
        "WHERE intensity > 0.1 AND (last_engaged_at IS NULL OR last_engaged_at < ?) "
        "ORDER BY intensity DESC LIMIT 10",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


_GEN1_SEARCH_KEY = "initiative:gen1_search_count:{date}"
_GEN1_SEARCH_DAILY_CAP = 2


def _gen1_search_available() -> bool:
    """True if the gen_level=1 daily search cap hasn't been reached."""
    today = datetime.now().date().isoformat()
    count = kv_get(_GEN1_SEARCH_KEY.format(date=today)) or 0
    return int(count) < _GEN1_SEARCH_DAILY_CAP


def consume_gen1_search_budget() -> None:
    """Increment the gen_level=1 daily search counter. Called from engine.py."""
    today = datetime.now().date().isoformat()
    key = _GEN1_SEARCH_KEY.format(date=today)
    count = kv_get(key) or 0
    kv_set(key, int(count) + 1)


# ---------------------------------------------------------------------------
# D-06 · Routine candidates
# ---------------------------------------------------------------------------

ROUTINE_PRESSURE = 0.8


class TimeWindow:
    def __init__(self, start: time, end: time):
        self.start = start
        self.end = end

    def contains(self, t: time) -> bool:
        return self.start <= t <= self.end


MORNING_WINDOW    = TimeWindow(time(8, 15), time(8, 45))
EVENING_WINDOW    = TimeWindow(time(20, 45), time(21, 15))
CONSOLIDATION_WIN = TimeWindow(time(2, 45), time(3, 45))  # Overlaps weekly window
WEEKLY_WINDOW     = TimeWindow(time(3, 0), time(4, 0))


def routine_candidates(now: datetime | None = None) -> list[CandidateAction]:
    """Emit time-based routine candidates."""
    if now is None:
        now = datetime.now()

    t = now.time()
    today = now.date().isoformat()
    this_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    candidates = []

    if MORNING_WINDOW.contains(t) and not kv_get(f"routine:morning_sent:{today}"):
        candidates.append(CandidateAction(
            tool="messages", verb="send_text",
            args={"body": ""},
            intent="Morning check-in with Teo — greet him and note anything relevant from overnight",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="morning_checkin",
        ))

    if EVENING_WINDOW.contains(t) and not kv_get(f"routine:evening_sent:{today}"):
        candidates.append(CandidateAction(
            tool="messages", verb="send_text",
            args={"body": ""},
            intent="Evening check-in with Teo — wrap up the day and see how he's doing",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="evening_checkin",
        ))

    if CONSOLIDATION_WIN.contains(t) and not kv_get(f"routine:consolidation:{today}"):
        candidates.append(CandidateAction(
            tool="self_tools", verb="trigger_consolidation",
            args={},
            intent="Run nightly memory consolidation and affect processing",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="consolidation",
        ))

    if now.weekday() == 6 and WEEKLY_WINDOW.contains(t) and not kv_get(f"routine:weekly_self_model:{this_week}"):
        candidates.append(CandidateAction(
            tool="self_tools", verb="trigger_weekly_self_model",
            args={},
            intent="Run weekly self-model introspection and update identity beliefs",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="weekly_self_model",
        ))

    log.debug("routine_candidates", count=len(candidates), time=str(t))
    return candidates


def mark_routine_done(source_id: str, now: datetime | None = None) -> None:
    """Mark a routine as completed for the current period."""
    if now is None:
        now = datetime.now()
    today = now.date().isoformat()
    this_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    key_map = {
        "morning_checkin":   f"routine:morning_sent:{today}",
        "evening_checkin":   f"routine:evening_sent:{today}",
        "consolidation":     f"routine:consolidation:{today}",
        "weekly_self_model": f"routine:weekly_self_model:{this_week}",
    }
    key = key_map.get(source_id)
    if key:
        kv_set(key, True)


# ---------------------------------------------------------------------------
# D-07 · Affect-driven "for fun" candidates
# ---------------------------------------------------------------------------
# These fire from mood state rather than interest intensity. They represent
# Chloe doing something because she feels like it — comfort music when low,
# creative writing when energised, rest when depleted. Each has a 3h KV
# cooldown so the same impulse doesn't loop.

_AFFECT_FUN_COOLDOWN_HOURS = 3

_AFFECT_FUN_ACTIVITIES: list[tuple[str, str, str, str, str]] = [
    # (id, tool, verb, intent, condition_desc)
    ("comfort_music",       "spotify",    "queue_track",
     "Put on something that matches where I'm at right now",
     "low_valence"),
    ("energised_write",     "notes",      "create",
     "Write something — a fragment, a thought, whatever's moving",
     "high_arousal_positive"),
    ("rabbit_hole",         "web_search", "search",
     "Chase a thought I've been half-holding all day",
     "high_openness"),
    ("slow_journal",        "notes",      "append",
     "Slow down and write something small — no agenda",
     "high_depletion"),
    ("draft_for_teo",       "notes",      "create",
     "Draft something I might share with Teo later",
     "high_social_pull"),
    ("mood_playlist",       "spotify",    "build_playlist",
     "Build a playlist that fits today's texture",
     "high_arousal_positive_evening"),
    # Extended set — finer-grained conditions
    ("morning_research",    "web_search", "search",
     "Follow a thread that's been on my mind since waking",
     "morning_active"),
    ("restless_look",       "web_search", "search",
     "Work off some restless energy — find something worth thinking about",
     "high_arousal_negative"),
    ("quiet_listen",        "spotify",    "queue_track",
     "Put something on low — just to have it in the background",
     "peaceful"),
    ("midday_capture",      "notes",      "append",
     "Catch a thought before the afternoon takes over",
     "midday_open"),
    ("evening_reflection",  "notes",      "append",
     "Write something small before the day closes",
     "evening_calm"),
    ("restless_playlist",   "spotify",    "build_playlist",
     "Build something for this particular restlessness",
     "high_arousal_negative_evening"),
]


def affect_driven_candidates(affect: dict | None = None) -> list[CandidateAction]:
    """Generate spontaneous 'for fun' candidates driven by current mood state.

    Each activity has a 3h cooldown to prevent loops. Conditions are checked
    against the current affect state.
    """
    if affect is None:
        affect = _load_affect_state()

    valence = float(affect.get("valence", 0.0))
    arousal = float(affect.get("arousal", 0.4))
    social_pull = float(affect.get("social_pull", 0.5))
    openness = float(affect.get("openness", 0.6))
    depletion = float(affect.get("depletion", 0.0))
    energy = float(affect.get("energy", 0.8))

    now = datetime.now()
    candidates = []

    for act_id, tool, verb, intent, condition in _AFFECT_FUN_ACTIVITIES:
        if _affect_fun_on_cooldown(act_id):
            continue

        pressure = _affect_fun_pressure(
            condition, valence, arousal, social_pull, openness, depletion, energy, now.hour
        )
        if pressure <= 0.0:
            continue

        args: dict = {}
        if tool == "web_search":
            args = {"query": ""}  # engine will compose from intent
        elif tool == "notes" and verb in ("append", "create"):
            slug = act_id
            if verb == "create":
                ts = int(now.timestamp()) % 10000
                args = {"path": f"journal/{slug}_{ts}.md", "text": ""}
            else:
                args = {"path": f"journal/{slug}.md", "text": ""}

        candidates.append(CandidateAction(
            tool=tool, verb=verb, args=args,
            intent=intent,
            pressure=pressure,
            source="affect",
            source_id=f"affect:{act_id}",
        ))

    log.debug("affect_fun_candidates", count=len(candidates))
    return candidates


def mark_affect_fun_done(act_id: str) -> None:
    """Stamp the cooldown KV for an affect-driven activity."""
    key = f"affect_fun:cooldown:{act_id}"
    kv_set(key, datetime.now().isoformat())


def _affect_fun_on_cooldown(act_id: str) -> bool:
    val = kv_get(f"affect_fun:cooldown:{act_id}")
    if not val:
        return False
    try:
        last = datetime.fromisoformat(str(val))
        return (datetime.now() - last).total_seconds() < _AFFECT_FUN_COOLDOWN_HOURS * 3600
    except Exception:
        return False


def _affect_fun_pressure(
    condition: str,
    valence: float, arousal: float, social_pull: float,
    openness: float, depletion: float, energy: float, hour: int,
) -> float:
    """Return a pressure in [0, 0.7] when the condition is met, else 0."""
    if condition == "low_valence":
        if valence < -0.2:
            return min(0.7, 0.4 + abs(valence) * 0.4)
    elif condition == "high_arousal_positive":
        if arousal > 0.6 and valence > 0.1:
            return min(0.65, (arousal - 0.6) * 1.5 + 0.3)
    elif condition == "high_openness":
        if openness > 0.65:
            return min(0.6, (openness - 0.65) * 1.2 + 0.3)
    elif condition == "high_depletion":
        if depletion > 0.45:
            return min(0.65, (depletion - 0.45) * 1.0 + 0.35)
    elif condition == "high_social_pull":
        if social_pull > 0.65:
            return min(0.6, (social_pull - 0.65) * 1.2 + 0.3)
    elif condition == "high_arousal_positive_evening":
        if arousal > 0.55 and valence > 0.1 and 18 <= hour < 23:
            return 0.45
    # Extended conditions
    elif condition == "morning_active":
        if 6 <= hour < 10 and energy > 0.6 and arousal > 0.4:
            return min(0.55, 0.3 + energy * 0.2 + arousal * 0.1)
    elif condition == "high_arousal_negative":
        if arousal > 0.65 and valence < -0.15:
            return min(0.6, (arousal - 0.65) * 1.3 + 0.3)
    elif condition == "peaceful":
        if arousal < 0.3 and valence >= 0.0 and depletion < 0.25 and energy > 0.4:
            return 0.38
    elif condition == "midday_open":
        if 11 <= hour < 15 and openness > 0.5 and energy > 0.45:
            return min(0.5, (openness - 0.5) * 0.9 + 0.3)
    elif condition == "evening_calm":
        if 20 <= hour < 23 and arousal < 0.45 and depletion < 0.4:
            return 0.38
    elif condition == "high_arousal_negative_evening":
        if 18 <= hour < 23 and arousal > 0.6 and valence < -0.1:
            return 0.42
    return 0.0


def _load_affect_state() -> dict:
    try:
        from chloe.state.db import get_connection
        row = get_connection().execute("SELECT * FROM affect_state LIMIT 1").fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}

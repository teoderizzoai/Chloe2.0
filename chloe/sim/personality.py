"""Personality tracking for the day simulator.

Takes a lightweight snapshot of Chloe's identity state at the end of each
simulated day, then calls Flash to write a 2-3 sentence character note
explaining what shifted and why. Produces a day-by-day changelog printed
at the end of the sim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DaySnapshot:
    day: int
    sim_date: str          # YYYY-MM-DD of the simulated day
    wants: list[dict]      # [{text, pressure}]
    tensions: list[dict]   # [{text, pressure}]
    interests: list[dict]  # [{label, intensity}]
    goals: list[dict]      # [{name, progress, status}]
    top_want_tags: dict    # {tag: count}
    affect: dict           # {valence, arousal}
    character_note: str = ""   # Flash-generated daily character note
    causes: list[str] = field(default_factory=list)  # what chat events drove changes


def take_snapshot(day: int, sim_date: str) -> DaySnapshot:
    from chloe.state.db import get_connection
    conn = get_connection()

    wants_rows = [dict(r) for r in conn.execute(
        "SELECT text, pressure, tags FROM inner_wants WHERE resolved=0 ORDER BY pressure DESC LIMIT 8"
    )]
    tensions_rows = [dict(r) for r in conn.execute(
        "SELECT text, pressure FROM inner_tensions WHERE resolved=0 ORDER BY pressure DESC LIMIT 8"
    )]
    interests_rows = [dict(r) for r in conn.execute(
        "SELECT label, intensity FROM interest_garden WHERE intensity>0 ORDER BY intensity DESC LIMIT 10"
    )]
    goals_rows = [dict(r) for r in conn.execute(
        "SELECT name, progress, status FROM inner_goals WHERE status NOT IN ('done','failed','stale')"
    )]
    affect_row = conn.execute(
        "SELECT valence, arousal FROM affect_state WHERE id=1"
    ).fetchone()

    import json
    tag_counts: dict[str, int] = {}
    for w in wants_rows:
        tags = w.get("tags", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        for t in tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    return DaySnapshot(
        day=day,
        sim_date=sim_date,
        wants=[{"text": w["text"], "pressure": round(w["pressure"], 2)} for w in wants_rows],
        tensions=[{"text": t["text"], "pressure": round(t["pressure"], 2)} for t in tensions_rows],
        interests=[{"label": i["label"], "intensity": round(i["intensity"], 2)} for i in interests_rows],
        goals=[{"name": g["name"], "progress": round(g["progress"], 2), "status": g["status"]} for g in goals_rows],
        top_want_tags=tag_counts,
        affect=dict(affect_row) if affect_row else {},
    )


async def generate_character_note(
    snap: DaySnapshot,
    prev: DaySnapshot | None,
    chat_events: list[str],
) -> str:
    """One Flash call: write a 2-3 sentence character note for the day."""
    from chloe.llm.gemini import get_client

    prev_interests = [i["label"] for i in (prev.interests if prev else [])]
    curr_interests = [i["label"] for i in snap.interests]
    new_interests = [i for i in curr_interests if i not in prev_interests]
    dropped_interests = [i for i in prev_interests if i not in curr_interests]

    prev_goals = {g["name"]: g["progress"] for g in (prev.goals if prev else [])}
    goal_changes = []
    for g in snap.goals:
        prev_p = prev_goals.get(g["name"])
        if prev_p is None:
            goal_changes.append(f"new goal: {g['name']}")
        elif abs(g["progress"] - prev_p) > 0.05:
            direction = "progressed" if g["progress"] > prev_p else "regressed"
            goal_changes.append(f"{g['name']}: {direction} ({prev_p:.0%}→{g['progress']:.0%})")

    prev_want_count = len(prev.wants) if prev else 0
    want_delta = len(snap.wants) - prev_want_count

    prompt = f"""You are observing Chloe's inner state at the end of simulated day {snap.day} ({snap.sim_date}).

What happened today (key chat moments):
{chr(10).join(f'- {e}' for e in chat_events) or '(nothing logged)'}

Inner state changes from yesterday:
- Wants: {want_delta:+d} (now {len(snap.wants)} active)
- Top want tags: {snap.top_want_tags}
- New interests: {new_interests or 'none'}
- Dropped interests: {dropped_interests or 'none'}
- Goal changes: {goal_changes or 'none'}
- Affect: valence={snap.affect.get('valence', 0):.2f}, arousal={snap.affect.get('arousal', 0):.2f}

Top wants right now: {[w['text'][:60] for w in snap.wants[:3]]}
Top interests right now: {curr_interests[:5]}

Write 2-3 sentences about what this day revealed about Chloe's character — not what happened, but what it says about who she is and what's changing in her. Be specific to the data. Write in third person, present tense. No fluff."""

    llm = get_client()
    try:
        from google import genai
        import os
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        resp = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
        )
        return (resp.text or "").strip()
    except Exception as exc:
        return f"(character note failed: {exc})"


def print_personality_log(snapshots: list[DaySnapshot]) -> None:
    print("\n" + "═" * 70)
    print("  PERSONALITY LOG — day-by-day character arc")
    print("═" * 70)

    for i, snap in enumerate(snapshots):
        prev = snapshots[i - 1] if i > 0 else None
        print(f"\n── Day {snap.day} ({snap.sim_date}) ──────────────────────────────")

        if snap.character_note:
            print(f"  {snap.character_note}")

        # Interest delta
        prev_int = {s["label"] for s in (prev.interests if prev else [])}
        curr_int = {s["label"] for s in snap.interests}
        gained = curr_int - prev_int
        lost = prev_int - curr_int
        if gained:
            print(f"  + interests: {', '.join(sorted(gained))}")
        if lost:
            print(f"  - interests: {', '.join(sorted(lost))}")

        # Goal delta
        prev_goals = {g["name"]: g["progress"] for g in (prev.goals if prev else [])}
        for g in snap.goals:
            prev_p = prev_goals.get(g["name"])
            if prev_p is None:
                print(f"  ◆ new goal: {g['name']} ({g['progress']:.0%})")
            elif abs(g["progress"] - prev_p) > 0.05:
                arrow = "↑" if g["progress"] > prev_p else "↓"
                print(f"  {arrow} goal: {g['name'][:50]} {prev_p:.0%}→{g['progress']:.0%}")

        # Want/tension count
        w_prev = len(prev.wants) if prev else 0
        t_prev = len(prev.tensions) if prev else 0
        w_delta = len(snap.wants) - w_prev
        t_delta = len(snap.tensions) - t_prev
        print(f"  wants {len(snap.wants)} ({w_delta:+d})  tensions {len(snap.tensions)} ({t_delta:+d})")

        if snap.top_want_tags:
            top = sorted(snap.top_want_tags.items(), key=lambda x: -x[1])[:3]
            print(f"  dominant want-tags: {', '.join(f'{k}({v})' for k,v in top)}")

    print("\n" + "═" * 70 + "\n")

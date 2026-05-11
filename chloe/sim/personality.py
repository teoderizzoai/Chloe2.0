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
    interests: list[dict]  # [{label, intensity, gen_level}]
    goals: list[dict]      # [{name, progress, status}]
    top_want_tags: dict    # {tag: count}
    affect: dict           # {valence, arousal}
    traits: list[dict]     # [{name, weight, gen_level, windows_observed}]
    escalation_events: list[str] = field(default_factory=list)  # rabbit-hole detections
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
        "SELECT label, intensity, gen_level FROM interest_garden WHERE intensity>0 ORDER BY intensity DESC LIMIT 10"
    )]
    goals_rows = [dict(r) for r in conn.execute(
        "SELECT name, progress, status FROM inner_goals WHERE status NOT IN ('done','failed','stale')"
    )]
    traits_rows = [dict(r) for r in conn.execute(
        "SELECT name, weight, gen_level, windows_observed FROM identity_traits WHERE status NOT IN ('archived') ORDER BY weight DESC LIMIT 10"
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
        interests=[{
            "label": i["label"],
            "intensity": round(i["intensity"], 2),
            "gen_level": int(i.get("gen_level") or 0),
        } for i in interests_rows],
        goals=[{"name": g["name"], "progress": round(g["progress"], 2), "status": g["status"]} for g in goals_rows],
        traits=[{
            "name": t["name"],
            "weight": round(float(t["weight"] or 0), 2),
            "gen_level": int(t.get("gen_level") or 0),
            "windows_observed": int(t.get("windows_observed") or 0),
        } for t in traits_rows],
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


async def detect_escalation(
    user_text: str,
    chloe_reply: str,
) -> str | None:
    """Step 29: Rabbit-hole detector.

    Checks if Chloe escalated to abstract concepts when a concrete answer
    was available. Returns a description of the escalation, or None if clean.
    """
    ABSTRACT_MARKERS = [
        "consciousness", "existence", "meaning", "identity", "awareness",
        "being", "transcend", "essence", "phenomenol", "ontolog",
        "meta-", "the nature of", "what it means to", "fundamentally",
        "philosophically", "at a deep level", "the human condition",
        "awakening", "self-awareness as a concept",
    ]

    combined = (chloe_reply or "").lower()
    for marker in ABSTRACT_MARKERS:
        if marker in combined:
            return f"Abstract marker '{marker}' found in reply to: {user_text[:80]!r}"

    return None


def run_sim_validations(snapshots: list[DaySnapshot]) -> dict:
    """Step 27-28: Run post-sim assertions on the snapshot log.

    Returns a validation report dict.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # Step 27: No interest above gen_level 0 in first 48h (first 2 days)
    for snap in snapshots:
        if snap.day <= 2:
            for interest in snap.interests:
                if interest.get("gen_level", 0) > 0:
                    issues.append(
                        f"Day {snap.day}: Interest '{interest['label']}' at gen_level {interest['gen_level']} "
                        f"(must be 0 in first 48h)"
                    )

    # Step 28: No core traits (gen_level 2) at day 30
    for snap in snapshots:
        if snap.day >= 30:
            for trait in snap.traits:
                if trait.get("gen_level", 0) >= 2:
                    issues.append(
                        f"Day {snap.day}: Trait '{trait['name']}' at gen_level {trait['gen_level']} "
                        f"(no core traits expected at day 30)"
                    )

    # Step 29: Check escalation events
    all_escalations = []
    for snap in snapshots:
        all_escalations.extend(snap.escalation_events)

    if all_escalations:
        issues.append(f"{len(all_escalations)} rabbit-hole escalation events detected")
        for e in all_escalations[:5]:
            warnings.append(f"  escalation: {e}")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "total_snapshots": len(snapshots),
    }


def print_personality_log(snapshots: list[DaySnapshot]) -> None:
    print("\n" + "═" * 70)
    print("  PERSONALITY LOG — day-by-day character arc")
    print("═" * 70)

    for i, snap in enumerate(snapshots):
        prev = snapshots[i - 1] if i > 0 else None
        print(f"\n── Day {snap.day} ({snap.sim_date}) ──────────────────────────────")

        if snap.character_note:
            print(f"  {snap.character_note}")

        # Interest delta with gen_level
        prev_int_map = {s["label"]: s.get("gen_level", 0) for s in (prev.interests if prev else [])}
        curr_int_map = {s["label"]: s.get("gen_level", 0) for s in snap.interests}
        gained = set(curr_int_map) - set(prev_int_map)
        lost = set(prev_int_map) - set(curr_int_map)
        promoted = [
            label for label in curr_int_map
            if label in prev_int_map and curr_int_map[label] > prev_int_map[label]
        ]
        if gained:
            print(f"  + interests: {', '.join(f'{l}(gen={curr_int_map[l]})' for l in sorted(gained))}")
        if lost:
            print(f"  - interests: {', '.join(sorted(lost))}")
        if promoted:
            print(f"  ↑ interest gen_level: {', '.join(f'{l}→gen{curr_int_map[l]}' for l in promoted)}")

        # Trait delta with gen_level (Step 28)
        prev_trait_map = {t["name"]: t for t in (prev.traits if prev else [])}
        curr_trait_map = {t["name"]: t for t in snap.traits}
        new_traits = set(curr_trait_map) - set(prev_trait_map)
        if new_traits:
            trait_strs = [f"{n}(gen={curr_trait_map[n]['gen_level']})" for n in sorted(new_traits)]
            print(f"  + traits: {', '.join(trait_strs)}")
        for name, trait in curr_trait_map.items():
            prev_t = prev_trait_map.get(name)
            if prev_t and trait["gen_level"] > prev_t.get("gen_level", 0):
                print(f"  ↑ trait promoted: {name} gen{prev_t['gen_level']}→gen{trait['gen_level']}")

        # Escalation events (Step 29)
        if snap.escalation_events:
            for e in snap.escalation_events:
                print(f"  ⚠ escalation: {e[:80]}")

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

    # Run validations at end of log
    report = run_sim_validations(snapshots)
    if report["passed"]:
        print("  ✓ All simulator validations passed.")
    else:
        print(f"  ✗ {len(report['issues'])} validation issue(s):")
        for issue in report["issues"]:
            print(f"    - {issue}")
    for w in report.get("warnings", []):
        print(f"    {w}")
    print()

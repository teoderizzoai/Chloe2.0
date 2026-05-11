"""Simulate a full day of Chloe's inner life.

Walks a controllable clock from `start` for `hours` simulated hours, in
`step_minutes` increments. At each step it:

  1. Applies any scripted chat / affect events whose timestamp <= now.
  2. Decays pressures every 10 simulated minutes.
  3. Runs reflect (the every-2h pass) every 2 simulated hours.
  4. Snapshots candidate counts/scores from the initiative engine — without
     firing actions (no gate, no compose, no real tool execution).

Designed to surface how wants / fears / tensions / interests / goals evolve
across a day, given a plausible chat + affect signal. Operates on a copy of
the live DB by default (`chloe.sim.db`) so prod state is untouched.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Fake clock — patched into modules that read datetime.now()
# ---------------------------------------------------------------------------

_FAKE_NOW: datetime | None = None


def _set_now(dt: datetime) -> None:
    global _FAKE_NOW
    _FAKE_NOW = dt


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        base = _FAKE_NOW if _FAKE_NOW is not None else datetime.now(tz)
        if tz is not None and base.tzinfo is None:
            return base.replace(tzinfo=tz)
        if tz is None and base.tzinfo is not None:
            return base.replace(tzinfo=None)
        return base

    @classmethod
    def utcnow(cls):  # type: ignore[override]
        if _FAKE_NOW is None:
            return datetime.utcnow()
        if _FAKE_NOW.tzinfo:
            return _FAKE_NOW.astimezone(timezone.utc).replace(tzinfo=None)
        return _FAKE_NOW


_PATCH_TARGETS = (
    "chloe.initiative.engine",
    "chloe.initiative.candidates",
    "chloe.initiative.curiosity",
    "chloe.inner.pressure",
    "chloe.reflect.every_2h",
    "chloe.identity.goals",
    "chloe.identity.interest_garden",
    "chloe.actions.audit",
)


def _install_fake_clock() -> dict[str, Any]:
    """Replace `datetime` in target modules with our fake. Returns originals
    for restore."""
    import importlib

    originals: dict[str, Any] = {}
    for mod_name in _PATCH_TARGETS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "datetime"):
            originals[mod_name] = mod.datetime
            mod.datetime = _FakeDatetime  # type: ignore[attr-defined]
    return originals


def _restore_clock(originals: dict[str, Any]) -> None:
    import importlib

    for mod_name, orig in originals.items():
        try:
            mod = importlib.import_module(mod_name)
            mod.datetime = orig  # type: ignore[attr-defined]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _bootstrap_clean_db(sim_db_path: Path) -> None:
    """Create a fresh DB at sim_db_path with migrations applied but no data.

    This gives the sim a truly blank slate — no real chat history, no old
    episodic memories, no prior inner state. The only thing carried over is
    the schema itself.
    """
    if sim_db_path.exists():
        sim_db_path.unlink()
    # Temporarily redirect DB so migrate() writes to the new file.
    from chloe.state import db as state_db
    if state_db._connection is not None:
        try:
            state_db._connection.close()
        except Exception:
            pass
        state_db._connection = None

    from chloe.state.db import migrate
    migrate(db_path=sim_db_path)
    # migrate() opens the connection and caches it — close so _redirect_db
    # can reopen cleanly with the right row_factory.
    if state_db._connection is not None:
        try:
            state_db._connection.close()
        except Exception:
            pass
        state_db._connection = None


def _redirect_db(sim_db_path: Path) -> None:
    """Force chloe.state.db to use a sim DB."""
    from chloe.state import db as state_db

    if state_db._connection is not None:
        try:
            state_db._connection.close()
        except Exception:
            pass
        state_db._connection = None

    conn = sqlite3.connect(str(sim_db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    state_db._connection = conn


# ---------------------------------------------------------------------------
# Default day script — plausible chat + affect events through a day
# ---------------------------------------------------------------------------

@dataclass
class ScriptedChat:
    minute_of_day: int  # 0–1439
    role: str           # "user" or "assistant"
    text: str


@dataclass
class ScriptedAffect:
    minute_of_day: int
    trigger: str
    valence_delta: float
    arousal_delta: float
    intensity: float


# Each list is one day's script. Index 0 = day 1, index 1 = day 2, etc.
# The last entry repeats for any days beyond the defined ones.
DAY_SCRIPTS: list[list[ScriptedChat | ScriptedAffect]] = [
    # ---- Day 1: anxious, curious, frustrated, wistful ----
    [
        ScriptedChat(8 * 60 + 30, "user", "morning. didn't sleep great"),
        ScriptedChat(8 * 60 + 31, "assistant", "rough one? what kept you up"),
        ScriptedChat(8 * 60 + 33, "user", "thinking about that contract thing again"),
        ScriptedAffect(8 * 60 + 35, "user_distressed", -0.3, 0.2, 0.5),

        ScriptedChat(11 * 60, "user", "found this paper on collective intelligence in slime molds, kind of blew my mind"),
        ScriptedChat(11 * 60 + 1, "assistant", "ok that's your kind of rabbit hole. what got you"),
        ScriptedChat(11 * 60 + 3, "user", "the idea that you can have intelligence with no center. no one in charge"),
        ScriptedAffect(11 * 60 + 4, "novel_idea", 0.4, 0.3, 0.6),

        ScriptedChat(16 * 60, "user", "stuck on the same bug for two hours"),
        ScriptedAffect(16 * 60 + 1, "frustration", -0.4, 0.4, 0.55),
        ScriptedChat(16 * 60 + 30, "user", "ok finally fixed it. it was a typo"),
        ScriptedAffect(16 * 60 + 31, "relief", 0.3, -0.2, 0.5),

        ScriptedChat(20 * 60, "user", "i feel like i never have time for the music thing anymore"),
        ScriptedAffect(20 * 60 + 1, "longing", -0.2, 0.1, 0.5),
        ScriptedChat(20 * 60 + 5, "assistant", "you've said that three times this month. that's a signal."),

        ScriptedChat(22 * 60 + 15, "user", "alright, calling it. night"),
        ScriptedChat(22 * 60 + 16, "assistant", "sleep. tomorrow's a new mess."),
    ],
    # ---- Day 2: contract resolved, energised, tries music again ----
    [
        ScriptedChat(8 * 60 + 45, "user", "contract thing got sorted. finally"),
        ScriptedChat(8 * 60 + 46, "assistant", "good. you've been carrying that for a while"),
        ScriptedAffect(8 * 60 + 48, "relief_news", 0.5, 0.1, 0.6),

        ScriptedChat(10 * 60 + 15, "user", "still thinking about that leaderless intelligence thing. applies to how teams work too"),
        ScriptedAffect(10 * 60 + 16, "contemplative", 0.2, 0.0, 0.4),

        ScriptedChat(13 * 60, "user", "actually ate lunch away from my desk for once"),
        ScriptedAffect(13 * 60 + 1, "calm_pleasant", 0.2, -0.1, 0.3),

        ScriptedChat(18 * 60 + 30, "user", "picked up the guitar for like 20 minutes. felt rusty but good"),
        ScriptedAffect(18 * 60 + 31, "quiet_satisfaction", 0.3, 0.1, 0.5),
        ScriptedChat(18 * 60 + 33, "assistant", "20 minutes is more than zero"),
        ScriptedChat(18 * 60 + 35, "user", "yeah. i forget how it actually helps"),

        ScriptedChat(21 * 60 + 30, "user", "decent day tbh"),
        ScriptedChat(21 * 60 + 31, "assistant", "noted. sleep well."),
    ],
    # ---- Day 3: focused, a small creative win, mild social longing ----
    [
        ScriptedChat(9 * 60, "user", "slept properly for once"),
        ScriptedAffect(9 * 60 + 1, "well_rested", 0.3, 0.2, 0.4),

        ScriptedChat(11 * 60 + 30, "user", "wrote something. not sure if it's good but it came out"),
        ScriptedAffect(11 * 60 + 31, "creative_flow", 0.4, 0.3, 0.6),
        ScriptedChat(11 * 60 + 33, "assistant", "what kind of thing"),
        ScriptedChat(11 * 60 + 34, "user", "just like a short piece. about the feeling of being in a city alone"),

        ScriptedChat(15 * 60, "user", "had a weird moment where i missed people. not anyone specific"),
        ScriptedAffect(15 * 60 + 1, "diffuse_longing", -0.15, 0.1, 0.45),
        ScriptedChat(15 * 60 + 2, "assistant", "that kind of missing is different. less sharp"),
        ScriptedChat(15 * 60 + 4, "user", "yeah exactly"),

        ScriptedChat(19 * 60 + 45, "user", "guitar again. starting to feel less bad at it"),
        ScriptedAffect(19 * 60 + 46, "quiet_satisfaction", 0.25, 0.1, 0.4),

        ScriptedChat(22 * 60, "user", "ok. night"),
        ScriptedChat(22 * 60 + 1, "assistant", "night."),
    ],
]

# Convenience flat list for the single-day default
DEFAULT_SCRIPT: list[ScriptedChat | ScriptedAffect] = DAY_SCRIPTS[0]


def script_for_day(day_num: int) -> list[ScriptedChat | ScriptedAffect]:
    """Return the script for a given 0-indexed day number. Repeats last day if beyond defined scripts."""
    if day_num < len(DAY_SCRIPTS):
        return DAY_SCRIPTS[day_num]
    return DAY_SCRIPTS[-1]


# ---------------------------------------------------------------------------
# Event injectors
# ---------------------------------------------------------------------------

def _inject_chat(role: str, text: str, ts: datetime) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    prefix = "Teo said" if role == "user" else "I said"
    body = f"{prefix}: {text}"
    conn.execute(
        """INSERT INTO memories (kind, text, source, source_ref, tags, salience, weight, created_at)
           VALUES ('episodic', ?, 'chat', 'sim:1', '["chat","sim"]', 0.3, 0.6, ?)""",
        (body, ts.isoformat()),
    )
    conn.commit()


def _inject_affect(ev: ScriptedAffect, ts: datetime) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        """INSERT INTO affect_records (trigger, valence_delta, arousal_delta, intensity, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (ev.trigger, ev.valence_delta, ev.arousal_delta, ev.intensity, ts.isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _inner_state_snapshot() -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    def n(q):
        return conn.execute(q).fetchone()[0]
    return {
        "wants": n("SELECT COUNT(*) FROM inner_wants WHERE resolved=0"),
        "fears": n("SELECT COUNT(*) FROM inner_fears WHERE resolved=0"),
        "tensions": n("SELECT COUNT(*) FROM inner_tensions WHERE resolved=0"),
        "interests": n("SELECT COUNT(*) FROM interest_garden WHERE intensity>0"),
        "goals": n("SELECT COUNT(*) FROM inner_goals WHERE status='active'"),
    }


def _list_inner() -> dict:
    from chloe.state.db import get_connection
    conn = get_connection()
    out: dict = {}
    out["wants"] = [dict(r) for r in conn.execute(
        "SELECT text, pressure FROM inner_wants WHERE resolved=0 ORDER BY pressure DESC LIMIT 10"
    )]
    out["tensions"] = [dict(r) for r in conn.execute(
        "SELECT text, pressure FROM inner_tensions WHERE resolved=0 ORDER BY pressure DESC LIMIT 10"
    )]
    out["interests"] = [dict(r) for r in conn.execute(
        "SELECT label, intensity FROM interest_garden WHERE intensity>0 ORDER BY intensity DESC LIMIT 10"
    )]
    out["goals"] = [dict(r) for r in conn.execute(
        "SELECT name, progress, status FROM inner_goals ORDER BY id DESC LIMIT 10"
    )]
    return out


def _snapshot_candidates(now: datetime) -> dict:
    """Build candidate pool without executing. Returns top-3 summary."""
    from chloe.initiative.candidates import (
        pressure_driven_candidates, goal_driven_candidates,
        interest_driven_candidates, routine_candidates,
    )
    try:
        from chloe.initiative.curiosity import curiosity_driven_candidates
        cur = curiosity_driven_candidates()
    except Exception:
        cur = []

    cands = (
        pressure_driven_candidates()
        + goal_driven_candidates()
        + interest_driven_candidates()
        + routine_candidates(now)
        + cur
    )
    cands.sort(key=lambda c: c.pressure, reverse=True)
    top = [
        {"tool": c.tool, "verb": c.verb, "src": c.source,
         "p": round(c.pressure, 2), "intent": c.intent[:50]}
        for c in cands[:3]
    ]
    return {"count": len(cands), "top": top}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

@dataclass
class SimResult:
    steps: int = 0
    reflect_runs: int = 0
    reflect_failures: int = 0
    chat_injected: int = 0
    affect_injected: int = 0
    initial_state: dict = field(default_factory=dict)
    final_state: dict = field(default_factory=dict)
    final_listing: dict = field(default_factory=dict)
    personality_snapshots: list = field(default_factory=list)


async def simulate_day(
    sim_db: Path,
    source_db: Path | None = None,
    start: datetime | None = None,
    hours: int = 24,
    step_minutes: int = 30,
    use_default_script: bool = True,
    extra_script: list | None = None,
    print_each_step: bool = True,
    clean: bool = False,
) -> SimResult:
    """Drive a simulated day (or multi-day run). Mutates `sim_db` only.

    clean=True  — create a fresh schema-only DB, no real memories bleed in.
    source_db   — copy this DB to sim_db before starting (ignored when clean=True).
    The default script repeats each simulated calendar day so multi-day runs
    get one full day's worth of chat/affect per 24h of sim time.
    """
    if clean:
        _bootstrap_clean_db(sim_db)
    elif source_db is not None:
        if source_db.exists():
            shutil.copy(source_db, sim_db)
        else:
            raise FileNotFoundError(f"source DB not found: {source_db}")
    elif not sim_db.exists():
        raise FileNotFoundError(
            f"sim DB not found at {sim_db} and no source_db provided to seed it"
        )

    _redirect_db(sim_db)
    originals = _install_fake_clock()

    result = SimResult()
    try:
        if start is None:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        result.initial_state = _inner_state_snapshot()
        if print_each_step:
            print(f"[sim] start={start.isoformat()} hours={hours} step={step_minutes}m db={sim_db}")
            print(f"[sim] initial state: {result.initial_state}")

        extra = extra_script or []

        # Track which (day_number, event_index) pairs have been injected.
        # Each calendar day loads its own script so content varies.
        injected: set[tuple[int, int]] = set()
        _cached_day_script: dict[int, list] = {}

        # Force reflect to think it's "due" at T+0.
        from chloe.state.kv import set as kv_set
        kv_set("reflect:last_run_at", (start - timedelta(hours=3)).astimezone(timezone.utc).isoformat())

        last_pressure = start
        last_reflect = start - timedelta(hours=3)

        # Personality tracking: snapshot at each calendar midnight.
        from chloe.sim.personality import take_snapshot, generate_character_note, print_personality_log
        prev_snapshot = None
        last_snapshot_day = -1
        # Collect chat events per day for the character note.
        day_chat_events: dict[int, list[str]] = {}

        end = start + timedelta(hours=hours)
        cur = start
        while cur < end:
            _set_now(cur)

            # Apply scripted events for the current calendar day.
            day_num = (cur - start).days
            tod_minutes = cur.hour * 60 + cur.minute
            midnight = cur.replace(hour=0, minute=0, second=0, microsecond=0)

            if day_num not in _cached_day_script:
                day_events = (script_for_day(day_num) if use_default_script else []) + extra
                _cached_day_script[day_num] = sorted(day_events, key=lambda e: e.minute_of_day)

            for idx, ev in enumerate(_cached_day_script[day_num]):
                key = (day_num, idx)
                if key in injected:
                    continue
                if ev.minute_of_day > tod_minutes:
                    break
                ev_ts = midnight + timedelta(minutes=ev.minute_of_day)
                if isinstance(ev, ScriptedChat):
                    _inject_chat(ev.role, ev.text, ev_ts)
                    result.chat_injected += 1
                    if ev.role == "user":
                        day_chat_events.setdefault(day_num, []).append(ev.text)
                elif isinstance(ev, ScriptedAffect):
                    _inject_affect(ev, ev_ts)
                    result.affect_injected += 1
                    day_chat_events.setdefault(day_num, []).append(f"[affect:{ev.trigger}]")
                injected.add(key)

            # Snapshot personality at the transition into a new calendar day.
            if day_num != last_snapshot_day and last_snapshot_day >= 0:
                snap_day = last_snapshot_day
                snap_date = (start + timedelta(days=snap_day)).strftime("%Y-%m-%d")
                snap = take_snapshot(snap_day + 1, snap_date)
                events_that_day = day_chat_events.get(snap_day, [])
                snap.character_note = await generate_character_note(snap, prev_snapshot, events_that_day)
                result.personality_snapshots.append(snap)
                prev_snapshot = snap
            last_snapshot_day = day_num

            if (cur - last_pressure) >= timedelta(minutes=10):
                from chloe.inner.pressure import decay_all_pressures
                decay_all_pressures()
                last_pressure = cur

            reflect_summary = ""
            if (cur - last_reflect) >= timedelta(hours=2):
                from chloe.reflect.every_2h import run_reflect
                try:
                    out = await run_reflect(force=True)
                    if out:
                        result.reflect_runs += 1
                        a = out.get("applied", {})
                        reflect_summary = (
                            f" reflect[w+{a.get('wants',0)} t+{a.get('tensions',0)} "
                            f"i+{a.get('interests',0)} g+{a.get('goals',0)}]"
                        )
                    else:
                        result.reflect_failures += 1
                        reflect_summary = " reflect[skipped]"
                except Exception as exc:
                    result.reflect_failures += 1
                    reflect_summary = f" reflect[err: {str(exc)[:40]}]"
                last_reflect = cur

            snap = _snapshot_candidates(cur)
            state = _inner_state_snapshot()

            if print_each_step:
                top_str = ""
                if snap["top"]:
                    t = snap["top"][0]
                    top_str = f" top={t['tool']}.{t['verb']}({t['src']},p={t['p']})"
                print(
                    f"[{cur.strftime('%H:%M')}] "
                    f"w={state['wants']} t={state['tensions']} i={state['interests']} "
                    f"g={state['goals']} cands={snap['count']}{top_str}{reflect_summary}"
                )

            result.steps += 1
            cur += timedelta(minutes=step_minutes)

        result.final_state = _inner_state_snapshot()
        result.final_listing = _list_inner()

        # Final day snapshot
        if last_snapshot_day >= 0:
            snap_date = (start + timedelta(days=last_snapshot_day)).strftime("%Y-%m-%d")
            final_snap = take_snapshot(last_snapshot_day + 1, snap_date)
            events_that_day = day_chat_events.get(last_snapshot_day, [])
            final_snap.character_note = await generate_character_note(final_snap, prev_snapshot, events_that_day)
            result.personality_snapshots.append(final_snap)

        if print_each_step:
            print(f"\n[sim] DONE. {result.steps} steps, {result.reflect_runs} reflect runs, "
                  f"{result.chat_injected} chat seeded, {result.affect_injected} affect seeded.")
            print(f"[sim] delta: {result.initial_state} -> {result.final_state}")
            if result.personality_snapshots:
                print_personality_log(result.personality_snapshots)
            print(f"[sim] final wants: {json.dumps(result.final_listing['wants'], indent=2)}")
            print(f"[sim] final tensions: {json.dumps(result.final_listing['tensions'], indent=2)}")
            print(f"[sim] final interests: {json.dumps(result.final_listing['interests'], indent=2)}")
            print(f"[sim] final goals: {json.dumps(result.final_listing['goals'], indent=2)}")
    finally:
        _restore_clock(originals)
        _set_now(None)  # type: ignore[arg-type]

    return result

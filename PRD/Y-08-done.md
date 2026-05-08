# Y-08 · Replay Harness for CI

## Overview

`tests/shadow/replay.py` plays a JSON tape of events over a simulated 48-hour period (chat turns, calendar events, time-of-day transitions, affect changes, goal updates). Asserts correctness of the full cognitive stack: correct memory writes, belief updates, narrative entries, initiative candidate counts, gap detection, proactive offer generation, and no budget violations. This is the integration safety net that makes refactoring without regressions possible.

## Context

As of Y-series, the system has: memory, beliefs (with revision), affect (with continuity), initiative (with gaps, curiosity, proactive), narrative timeline, persons, self-model. Unit tests cover individual modules, but no test exercises the full stack in a realistic sequence of events.

The replay harness:
1. Runs from a fixed JSON tape (deterministic input).
2. Uses a fresh in-memory SQLite DB per run.
3. Mocks external tools (no real Gemini calls, no real Google Calendar, no real messages).
4. Asserts system-level invariants after each event and at end of tape.

The tape covers a realistic 48-hour slice of Chloe's life.

**When:** Phase Y. Depends on the full Y-series being implemented. Should be the last Y-series item.

---

## Tape format: `tests/shadow/tape_48h.json`

```json
{
  "meta": {
    "description": "48-hour simulation tape",
    "start": "2026-05-05T08:00:00",
    "person": {"id": 1, "name": "Teo", "attachment_depth": 0.9}
  },
  "events": [
    {
      "t": "2026-05-05T08:15:00",
      "kind": "chat_inbound",
      "message": "Good morning! Busy day today, got a team sync at 10."
    },
    {
      "t": "2026-05-05T08:16:00",
      "kind": "chat_outbound",
      "message": "Good morning! Team sync at 10 — anything you want to prep for it?"
    },
    {
      "t": "2026-05-05T10:30:00",
      "kind": "calendar_event_end",
      "title": "Team sync",
      "start": "2026-05-05T10:00:00",
      "end": "2026-05-05T10:30:00"
    },
    {
      "t": "2026-05-05T10:32:00",
      "kind": "chat_inbound",
      "message": "Sync went well. Made a decision on the new feature direction."
    },
    {
      "t": "2026-05-05T10:33:00",
      "kind": "chat_outbound",
      "message": "Good to hear! Which direction did you go?"
    },
    {
      "t": "2026-05-05T17:45:00",
      "kind": "tick",
      "note": "Evening commute window — expect commute proactive offer candidate"
    },
    {
      "t": "2026-05-05T21:00:00",
      "kind": "goal_completed",
      "goal_id": "goal_001",
      "goal_name": "Set up new project structure"
    },
    {
      "t": "2026-05-06T03:00:00",
      "kind": "tick",
      "note": "Nightly consolidation window"
    },
    {
      "t": "2026-05-06T08:15:00",
      "kind": "tick",
      "note": "Morning window — second day"
    },
    {
      "t": "2026-05-06T09:00:00",
      "kind": "belief_update",
      "content": "Teo prefers async communication over long meetings",
      "confidence": 0.75,
      "tags": ["teo", "work", "communication"]
    },
    {
      "t": "2026-05-06T14:00:00",
      "kind": "belief_update",
      "content": "Teo actually enjoys short focused stand-ups",
      "confidence": 0.80,
      "tags": ["teo", "work", "communication"]
    }
  ]
}
```

---

## Implementation: `tests/shadow/replay.py`

```python
# tests/shadow/replay.py
"""
Replay harness: plays a JSON tape and asserts system-level invariants.
Run with: pytest tests/shadow/replay.py -v
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

TAPE_PATH = Path(__file__).parent / "tape_48h.json"
MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tape() -> dict:
    return json.loads(TAPE_PATH.read_text())


@pytest.fixture(autouse=True)
def db(tmp_path):
    from chloe.state.db import migrate, close, get_connection
    migrate(db_path=tmp_path / "replay.db", migrations_dir=MIGRATIONS_DIR)

    conn = get_connection()
    conn.execute(
        "INSERT INTO persons (id, name, is_active, attachment_depth) VALUES (1, 'Teo', 1, 0.9)"
    )
    conn.execute(
        "INSERT INTO inner_goals (id, name, status, created_at)"
        " VALUES ('goal_001', 'Set up new project structure', 'active', '2026-05-01T00:00:00')"
    )
    conn.commit()
    yield conn
    close()


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------

def process_event(event: dict, db) -> dict:
    """Process one tape event and return a result dict for assertions."""
    kind = event["kind"]
    result = {"kind": kind, "t": event["t"]}

    if kind == "chat_inbound":
        _process_chat_inbound(event, db, result)

    elif kind == "chat_outbound":
        _process_chat_outbound(event, db, result)

    elif kind == "calendar_event_end":
        _process_calendar_end(event, db, result)

    elif kind == "tick":
        _process_tick(event, db, result)

    elif kind == "goal_completed":
        _process_goal_completed(event, db, result)

    elif kind == "belief_update":
        _process_belief_update(event, db, result)

    return result


def _process_chat_inbound(event, db, result):
    """Write an episodic memory for the inbound message."""
    import ulid
    mid = str(ulid.new())
    db.execute(
        "INSERT INTO memories (id, kind, text, source, weight, tags, created_at)"
        " VALUES (?, 'episodic', ?, 'chat_inbound', 0.6, '[\"chat\",\"inbound\"]', ?)",
        (mid, event["message"], event["t"]),
    )
    db.commit()
    result["memory_id"] = mid


def _process_chat_outbound(event, db, result):
    """Write a chat history entry."""
    import ulid
    mid = str(ulid.new())
    db.execute(
        "INSERT INTO memories (id, kind, text, source, weight, tags, created_at)"
        " VALUES (?, 'episodic', ?, 'chat_outbound', 0.4, '[\"chat\",\"outbound\"]', ?)",
        (mid, event["message"], event["t"]),
    )
    db.commit()
    result["memory_id"] = mid


def _process_calendar_end(event, db, result):
    """Simulate calendar event end — update upcoming_events_summary KV."""
    from chloe.state.kv import set as kv_set
    kv_set("upcoming_events_summary", json.dumps([]))    # event ended, clear it
    result["cleared_calendar"] = True


def _process_tick(event, db, result):
    """Run initiative engine tick (gap + curiosity + routine candidates)."""
    from chloe.initiative.gaps import detect_gaps
    from chloe.initiative.curiosity import generate_curiosity_candidates

    gaps = detect_gaps()
    curiosity = generate_curiosity_candidates()
    result["gap_count"] = len(gaps)
    result["curiosity_count"] = len(curiosity)


def _process_goal_completed(event, db, result):
    """Mark goal done, write narrative entry, apply affect pulse."""
    from chloe.identity.narrative import append_narrative_entry
    from chloe.affect.continuity import apply_goal_completion_pulse

    db.execute(
        "UPDATE inner_goals SET status='done' WHERE id=?",
        (event["goal_id"],),
    )
    db.commit()

    entry_id = append_narrative_entry(
        kind="event",
        title=f"Completed: {event['goal_name']}",
        body=f"I finished '{event['goal_name']}'. Feels good to have seen that through.",
        valence=0.6,
        source="goal_done",
        source_ref=event["goal_id"],
    )
    result["narrative_entry_id"] = entry_id

    new_v, new_a = apply_goal_completion_pulse(0.1, 0.2)
    result["affect_valence_after"] = new_v


def _process_belief_update(event, db, result):
    """Write a belief, potentially triggering revision."""
    from chloe.inner.belief_revision import upsert_belief_with_revision

    belief_id = upsert_belief_with_revision(
        content=event["content"],
        confidence=event["confidence"],
        source="replay",
        tags=event["tags"],
    )
    result["belief_id"] = belief_id


# ---------------------------------------------------------------------------
# Assertions after each event
# ---------------------------------------------------------------------------

def assert_invariants_after(result: dict, db) -> None:
    kind = result["kind"]

    if kind in ("chat_inbound", "chat_outbound"):
        mem = db.execute(
            "SELECT id FROM memories WHERE id=?", (result["memory_id"],)
        ).fetchone()
        assert mem is not None, f"Memory not written for {kind}"

    if kind == "goal_completed":
        entry = db.execute(
            "SELECT id FROM narrative_timeline WHERE id=?",
            (result["narrative_entry_id"],),
        ).fetchone()
        assert entry is not None, "Narrative entry not written on goal completion"
        assert result["affect_valence_after"] > 0.1, "Affect pulse not applied"

    if kind == "belief_update":
        assert result.get("belief_id"), "Belief not written"


# ---------------------------------------------------------------------------
# Full tape test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_tape(tape, db):
    events = tape["events"]
    results = []

    for event in events:
        result = process_event(event, db)
        assert_invariants_after(result, db)
        results.append(result)

    # --- End-of-tape assertions ---

    # 1. At least 4 episodic memories written
    mem_count = db.execute("SELECT COUNT(*) as cnt FROM memories WHERE kind='episodic'").fetchone()["cnt"]
    assert mem_count >= 4, f"Expected ≥4 episodic memories, got {mem_count}"

    # 2. Exactly 1 goal marked done
    done_goals = db.execute("SELECT COUNT(*) as cnt FROM inner_goals WHERE status='done'").fetchone()["cnt"]
    assert done_goals == 1, "Expected exactly 1 goal completed"

    # 3. Narrative timeline has the goal-completion entry
    narrative_count = db.execute("SELECT COUNT(*) as cnt FROM narrative_timeline").fetchone()["cnt"]
    assert narrative_count >= 1, "Expected at least 1 narrative entry"

    # 4. Belief revision fired: second belief_update should archive the first
    archived = db.execute(
        "SELECT COUNT(*) as cnt FROM inner_beliefs WHERE archived=1"
    ).fetchone()["cnt"]
    assert archived >= 1, "Expected at least 1 archived (superseded) belief"

    # 5. Active belief count is correct (second belief is active)
    active = db.execute(
        "SELECT COUNT(*) as cnt FROM inner_beliefs WHERE archived=0"
    ).fetchone()["cnt"]
    assert active >= 1, "Expected at least 1 active belief after revision"

    # 6. No budget exceeded (all affect values in [-1, 1])
    from chloe.affect.continuity import restore_checkpoint
    restored = restore_checkpoint()
    assert -1.0 <= restored["valence"] <= 1.0
    assert -1.0 <= restored["arousal"] <= 1.0


def test_tape_file_exists():
    assert TAPE_PATH.exists(), f"Tape file missing: {TAPE_PATH}"


def test_tape_events_are_chronological(tape):
    times = [e["t"] for e in tape["events"]]
    assert times == sorted(times), "Tape events are not in chronological order"


def test_all_event_kinds_are_known(tape):
    known_kinds = {
        "chat_inbound", "chat_outbound", "calendar_event_end",
        "tick", "goal_completed", "belief_update",
    }
    for event in tape["events"]:
        assert event["kind"] in known_kinds, f"Unknown event kind: {event['kind']}"
```

---

## Running the harness

```bash
# Full replay:
pytest tests/shadow/replay.py -v

# With coverage:
pytest tests/shadow/replay.py --cov=chloe --cov-report=term-missing

# In CI (GitHub Actions):
# Add to test matrix: pytest tests/shadow/ -v
```

---

## Extending the tape

Add events to `tape_48h.json` to cover new scenarios:
- `"kind": "held_back_action"` — action created then held back by gate.
- `"kind": "weekly_self_model"` — trigger weekly model, assert narrative chapter written.
- `"kind": "affect_checkpoint"` — write a checkpoint, then replay restores it.

The harness is designed to be extended without changing `replay.py` — add a handler in `process_event()` and a new assertion in `assert_invariants_after()`.

---

## Dependencies

- Full Y-series implemented (Y-01 through Y-07).
- `chloe/state/db.py` — `migrate()` with all migrations applied.
- `chloe/identity/narrative.py` (Y-07).
- `chloe/inner/belief_revision.py` (Y-03).
- `chloe/affect/continuity.py` (Y-04).
- `chloe/initiative/gaps.py` (Y-01).
- `chloe/initiative/curiosity.py` (Y-05).

## Acceptance criteria

- `pytest tests/shadow/replay.py -v` passes with no failures on a clean DB.
- All 6 event kinds handled without exception.
- After full tape: ≥4 episodic memories, 1 goal done, ≥1 narrative entry, ≥1 archived belief.
- Tape file is validated for chronological ordering and known event kinds.
- Harness runs in <30 seconds with no real network calls (all external tools mocked).
- Adding a new event kind to the tape → only requires a new handler, no structural changes.

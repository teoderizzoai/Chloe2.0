"""
Phase Y 48-hour replay harness.
Exercises: memory writes, belief revision, narrative entries, gap/curiosity detection,
proactive offers, affect continuity — all in a deterministic sequence.

Run with: pytest tests/shadow/test_replay_48h.py -v -m shadow
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime
from pathlib import Path

TAPE_PATH       = Path(__file__).parent / "tape_48h.json"
MIGRATIONS_DIR  = Path(__file__).parents[2] / "chloe/state/migrations"

pytestmark = pytest.mark.shadow


@pytest.fixture(autouse=True)
def db(tmp_path):
    from chloe.state.db import migrate, close, get_connection
    migrate(db_path=tmp_path / "replay48.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection()
    conn.execute(
        "INSERT INTO persons (id, name, is_active, attachment_depth) VALUES (1, 'Teo', 1, 0.9)"
    )
    conn.execute(
        "INSERT INTO inner_goals (id, name, status, created_at)"
        " VALUES (1, 'Set up new project structure', 'active', '2026-05-01T00:00:00')"
    )
    conn.commit()
    yield conn
    close()


def _load_tape() -> dict:
    return json.loads(TAPE_PATH.read_text())


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------

def process_event(event: dict, db) -> dict:
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
    cursor = db.execute(
        "INSERT INTO memories (kind, text, source, weight, tags, created_at)"
        " VALUES ('episodic', ?, 'chat_inbound', 0.6, '[\"chat\",\"inbound\"]', ?)",
        (event["message"], event["t"]),
    )
    db.commit()
    result["memory_id"] = cursor.lastrowid


def _process_chat_outbound(event, db, result):
    cursor = db.execute(
        "INSERT INTO memories (kind, text, source, weight, tags, created_at)"
        " VALUES ('episodic', ?, 'chat_outbound', 0.4, '[\"chat\",\"outbound\"]', ?)",
        (event["message"], event["t"]),
    )
    db.commit()
    result["memory_id"] = cursor.lastrowid


def _process_calendar_end(event, db, result):
    from chloe.state.kv import set as kv_set
    kv_set("upcoming_events_summary", json.dumps([]))
    result["cleared_calendar"] = True


def _process_tick(event, db, result):
    from chloe.initiative.gaps import detect_gaps
    from chloe.initiative.curiosity import generate_curiosity_candidates
    gaps = detect_gaps()
    curiosity = generate_curiosity_candidates()
    result["gap_count"] = len(gaps)
    result["curiosity_count"] = len(curiosity)


def _process_goal_completed(event, db, result):
    from chloe.identity.narrative import append_narrative_entry
    from chloe.affect.continuity import apply_goal_completion_pulse

    db.execute("UPDATE inner_goals SET status='done' WHERE id=?", (int(event["goal_id"]),))
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
    from chloe.inner.belief_revision import upsert_belief_with_revision
    belief_id = upsert_belief_with_revision(
        content=event["content"],
        confidence=event["confidence"],
        source="replay",
        tags=event["tags"],
    )
    result["belief_id"] = belief_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_full_48h_replay(db):
    tape = _load_tape()
    results = [process_event(ev, db) for ev in tape["events"]]

    memory_count = db.execute("SELECT COUNT(*) AS cnt FROM memories").fetchone()["cnt"]
    assert memory_count >= 4, f"Expected ≥4 memories, got {memory_count}"

    goal_row = db.execute("SELECT status FROM inner_goals WHERE id=1").fetchone()
    assert goal_row["status"] == "done"

    narrative_count = db.execute("SELECT COUNT(*) AS cnt FROM narrative_timeline").fetchone()["cnt"]
    assert narrative_count >= 1, "Expected at least one narrative entry from goal completion"

    belief_count = db.execute("SELECT COUNT(*) AS cnt FROM inner_beliefs").fetchone()["cnt"]
    assert belief_count >= 1


def test_belief_revision_fires_on_contradiction(db):
    tape = _load_tape()
    belief_results = [
        process_event(ev, db)
        for ev in tape["events"]
        if ev["kind"] == "belief_update"
    ]
    assert len(belief_results) == 2

    archived = db.execute(
        "SELECT COUNT(*) AS cnt FROM inner_beliefs WHERE archived=1"
    ).fetchone()["cnt"]
    assert archived >= 1, "Expected the first belief to be archived after contradiction"

    revision_memory = db.execute(
        "SELECT * FROM memories WHERE source='belief_revision'"
    ).fetchone()
    assert revision_memory is not None, "Expected a belief-revision autobiographical memory"


def test_goal_completion_creates_narrative_and_affect_pulse(db):
    tape = _load_tape()
    goal_events = [ev for ev in tape["events"] if ev["kind"] == "goal_completed"]
    assert len(goal_events) == 1

    result = process_event(goal_events[0], db)

    assert "narrative_entry_id" in result
    assert result["affect_valence_after"] > 0.1

    row = db.execute(
        "SELECT * FROM narrative_timeline WHERE id=?", (result["narrative_entry_id"],)
    ).fetchone()
    assert row is not None
    assert row["kind"] == "event"
    assert row["valence"] == pytest.approx(0.6)

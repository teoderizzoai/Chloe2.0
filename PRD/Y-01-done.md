# Y-01 · Gap Detection Engine

## Overview

A dedicated `GapDetector` that scans person models, belief store, and active goals for missing or stale knowledge, then emits `KnowledgeGap` records into the initiative engine as a new `source="gap"` candidate type. Gaps have priority scores; high-priority gaps surface as proactive questions during natural conversation openings rather than out-of-context interrogations.

## Context

The initiative engine generates candidates from wants, fears, goals, routines, and opportunities (D-03–D-07). But there's no path that says "I don't know something important and I should find out." Chloe can have a goal linked to a person, realize she doesn't know their schedule, and never flag that as missing — the goal just stalls.

Gap detection closes this loop:
- **Person gaps**: a field on a person model hasn't been updated in >30 days, or was never filled.
- **Belief gaps**: a belief about a person has confidence <0.4 and hasn't been revisited in >14 days.
- **Goal gaps**: an active goal references a person/constraint whose current state is unknown.

Gaps do NOT generate direct actions (no unsolicited messages). They surface as low-to-medium pressure `CandidateAction` with `tool="gap_flag"` — a synthetic tool that the deliberation step converts into a note to self or a conversational prompt on the next chat turn.

**When:** Phase Y (after H-series complete). Depends on persons store (A-series), belief store (inner/), and initiative engine (D-series).

---

## New module: `initiative/gaps.py`

```python
# chloe/initiative/gaps.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("initiative.gaps")

GAP_STALE_PERSON_DAYS = 30
GAP_STALE_BELIEF_DAYS = 14
GAP_STALE_GOAL_DAYS = 7


@dataclass
class KnowledgeGap:
    subject: str                                                      # human-readable topic
    description: str                                                   # "No food preference recorded in 30 days"
    priority: float                                                    # 0.0–1.0
    kind: Literal["person", "belief", "goal"]
    reference_id: str                                                  # person_id, belief_id, or goal_id
    suggested_framing: str                                             # how to ask / surface this


def detect_gaps() -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    gaps.extend(_person_gaps())
    gaps.extend(_belief_gaps())
    gaps.extend(_goal_gaps())
    gaps.sort(key=lambda g: g.priority, reverse=True)
    log.debug("gaps_detected", total=len(gaps))
    return gaps


# ---------------------------------------------------------------------------
# Person gaps — fields that are empty or stale
# ---------------------------------------------------------------------------

PERSON_FIELDS = [
    ("schedule",          "schedule/routine",      0.7, "What does your week usually look like?"),
    ("food_preferences",  "food preferences",      0.5, "Any foods you're really into lately?"),
    ("sleep_pattern",     "sleep pattern",         0.4, "How's your sleep been?"),
    ("work_context",      "work situation",        0.6, "How's work going?"),
    ("mood_baseline",     "recent emotional state", 0.5, "How are you doing overall?"),
]


def _person_gaps() -> list[KnowledgeGap]:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=GAP_STALE_PERSON_DAYS)).isoformat()
    persons = conn.execute("SELECT id, name FROM persons WHERE is_active = 1").fetchall()

    gaps = []
    for person in persons:
        pid = person["id"]
        pname = person["name"]

        for field_name, label, base_priority, framing in PERSON_FIELDS:
            row = conn.execute(
                """
                SELECT updated_at FROM person_fields
                WHERE person_id = ? AND field_name = ?
                """,
                (pid, field_name),
            ).fetchone()

            if row is None:
                # Field never recorded
                gaps.append(KnowledgeGap(
                    subject=f"{pname}: {label}",
                    description=f"No {label} recorded for {pname}.",
                    priority=base_priority,
                    kind="person",
                    reference_id=str(pid),
                    suggested_framing=framing,
                ))
            elif row["updated_at"] < cutoff:
                gaps.append(KnowledgeGap(
                    subject=f"{pname}: {label}",
                    description=f"{label.capitalize()} for {pname} is over {GAP_STALE_PERSON_DAYS} days old.",
                    priority=base_priority * 0.7,
                    kind="person",
                    reference_id=str(pid),
                    suggested_framing=framing,
                ))

    return gaps


# ---------------------------------------------------------------------------
# Belief gaps — low-confidence beliefs not revisited recently
# ---------------------------------------------------------------------------

def _belief_gaps() -> list[KnowledgeGap]:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=GAP_STALE_BELIEF_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id, content, confidence, updated_at
        FROM inner_beliefs
        WHERE confidence < 0.4
          AND updated_at < ?
          AND archived = 0
        ORDER BY confidence ASC
        LIMIT 5
        """,
        (cutoff,),
    ).fetchall()

    gaps = []
    for row in rows:
        gaps.append(KnowledgeGap(
            subject=f"uncertain belief: {row['content'][:60]}",
            description=f"Belief (confidence={row['confidence']:.2f}) hasn't been revisited in {GAP_STALE_BELIEF_DAYS}+ days.",
            priority=0.3 + (0.4 - row["confidence"]) * 0.5,
            kind="belief",
            reference_id=str(row["id"]),
            suggested_framing="Pay attention for information that might confirm or revise this.",
        ))

    return gaps


# ---------------------------------------------------------------------------
# Goal gaps — active goals with missing context
# ---------------------------------------------------------------------------

def _goal_gaps() -> list[KnowledgeGap]:
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=GAP_STALE_GOAL_DAYS)).isoformat()

    rows = conn.execute(
        """
        SELECT id, name, last_action_at, missing_context
        FROM inner_goals
        WHERE status NOT IN ('done', 'failed', 'stale')
          AND (last_action_at IS NULL OR last_action_at < ?)
          AND missing_context IS NOT NULL
        """,
        (cutoff,),
    ).fetchall()

    gaps = []
    for row in rows:
        gaps.append(KnowledgeGap(
            subject=f"goal '{row['name']}': missing context",
            description=row["missing_context"],
            priority=0.65,
            kind="goal",
            reference_id=str(row["id"]),
            suggested_framing=f"To move forward on '{row['name']}', I need: {row['missing_context']}",
        ))

    return gaps
```

---

## Integration with initiative engine

In `initiative/candidates.py`, add:

```python
from chloe.initiative.gaps import detect_gaps, KnowledgeGap
from chloe.initiative.candidates import CandidateAction


def gap_driven_candidates() -> list[CandidateAction]:
    """Surface knowledge gaps as low-pressure candidates for the deliberation step."""
    gaps = detect_gaps()
    candidates = []

    for gap in gaps[:3]:    # cap: deliberation sees at most 3 gaps per tick
        candidates.append(CandidateAction(
            tool="gap_flag",
            verb="surface",
            args={
                "subject": gap.subject,
                "description": gap.description,
                "suggested_framing": gap.suggested_framing,
                "kind": gap.kind,
                "reference_id": gap.reference_id,
            },
            intent=f"Notice a knowledge gap: {gap.description}",
            pressure=gap.priority * 0.6,    # gaps are softer than wants/fears
            source="gap",
            source_id=f"gap:{gap.reference_id}:{gap.kind}",
        ))

    return candidates
```

In `initiative/engine.py`, add `gap_driven_candidates()` to the candidate aggregation list alongside `pressure_driven_candidates()`, `goal_driven_candidates()`, etc.

---

## `gap_flag` tool handling

The `gap_flag` tool is not a real external tool. The deliberation step handles it:

```python
# In actions/deliberate.py:
if action.tool == "gap_flag":
    # Don't execute — store the gap as a pending note to self.
    # On next chat turn, inject as a soft hint to the model:
    # "You haven't learned Teo's schedule. If a natural opening comes up, ask."
    from chloe.state.kv import set as kv_set
    import json
    kv_set(
        "pending_gap_hints",
        json.dumps(action.args, ensure_ascii=False)[:500],
    )
    return Verdict(approved=False, reason="gap flagged for conversational surfacing")
```

In `llm/prompts.py`, inject the pending gap hint into the system prompt tail:

```python
hint = kv_get("pending_gap_hints")
if hint:
    gap = json.loads(hint)
    gap_block = (
        f"\n\n[Soft note to self: {gap.get('description', '')} "
        f"If a natural opening comes up, you might ask: \"{gap.get('suggested_framing', '')}\" "
        f"Don't force it.]"
    )
    kv_set("pending_gap_hints", "")    # consume after injecting once
```

---

## Schema addition

Add `missing_context` column to `inner_goals` migration:

```sql
-- In a new migration file:
ALTER TABLE inner_goals ADD COLUMN missing_context TEXT;
```

Add `person_fields` table if not present:

```sql
CREATE TABLE IF NOT EXISTS person_fields (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  INTEGER NOT NULL REFERENCES persons(id),
    field_name TEXT    NOT NULL,
    value      TEXT,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(person_id, field_name)
);
```

---

## Testing

### `tests/unit/test_gap_detection.py`

```python
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from chloe.state.db import migrate, close, get_connection
from chloe.initiative.gaps import detect_gaps, _person_gaps, _belief_gaps

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_person(db, name="Teo"):
    db.execute(
        "INSERT INTO persons (name, is_active, attachment_depth) VALUES (?, 1, 0.9)",
        (name,),
    )
    db.commit()
    return db.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()["id"]


def test_no_person_fields_yields_gaps(db):
    _insert_person(db, "Teo")
    gaps = _person_gaps()
    assert len(gaps) > 0
    assert all(g.kind == "person" for g in gaps)


def test_fresh_person_field_no_gap(db):
    pid = _insert_person(db)
    db.execute(
        "INSERT INTO person_fields (person_id, field_name, value, updated_at)"
        " VALUES (?, 'schedule', 'M-F 9-6', datetime('now'))",
        (pid,),
    )
    db.commit()
    gaps = _person_gaps()
    subjects = [g.subject for g in gaps]
    assert not any("schedule/routine" in s for s in subjects)


def test_stale_person_field_yields_gap(db):
    pid = _insert_person(db)
    old = (datetime.utcnow() - timedelta(days=40)).isoformat()
    db.execute(
        "INSERT INTO person_fields (person_id, field_name, value, updated_at)"
        " VALUES (?, 'schedule', 'old', ?)",
        (pid, old),
    )
    db.commit()
    gaps = _person_gaps()
    assert any("schedule" in g.subject for g in gaps)


def test_low_confidence_belief_yields_gap(db):
    old = (datetime.utcnow() - timedelta(days=20)).isoformat()
    db.execute(
        "INSERT INTO inner_beliefs (content, confidence, updated_at, archived)"
        " VALUES ('Teo prefers tea', 0.2, ?, 0)",
        (old,),
    )
    db.commit()
    gaps = _belief_gaps()
    assert len(gaps) == 1
    assert gaps[0].kind == "belief"


def test_detect_gaps_sorted_by_priority(db):
    pid = _insert_person(db)
    gaps = detect_gaps()
    priorities = [g.priority for g in gaps]
    assert priorities == sorted(priorities, reverse=True)


def test_gap_candidates_capped_at_three(db):
    # Insert 10 persons with no fields
    for i in range(10):
        _insert_person(db, f"Person{i}")
    from chloe.initiative.gaps import detect_gaps
    from chloe.initiative.candidates import gap_driven_candidates
    candidates = gap_driven_candidates()
    assert len(candidates) <= 3
```

---

## Dependencies

- A-series persons store (`persons` table, `person_fields` table).
- D-series initiative engine (candidate aggregation in `engine.py`).
- `inner_beliefs` table (inner/ module).
- `inner_goals` table with `missing_context` column (new migration).

## Acceptance criteria

- A person with no recorded fields → at least one `KnowledgeGap(kind="person")` per missing field.
- A person field updated within 30 days → no gap for that field.
- A belief with `confidence < 0.4` not updated in 14 days → `KnowledgeGap(kind="belief")`.
- `gap_driven_candidates()` returns at most 3 candidates per tick.
- `gap_flag` candidate sets `pending_gap_hints` in KV and is consumed on the next chat turn prompt.
- `detect_gaps()` output sorted by `priority` descending.

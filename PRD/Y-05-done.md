# Y-05 · Curiosity Engine

## Overview

A `CuriosityEngine` that generates questions Chloe is genuinely curious about — driven by relationship depth, topic recency, and interest intensity, not by task gaps. Unlike gap detection (Y-01, need-driven: "I need X to do Y"), curiosity is interest-driven: "It's been 3 weeks since you mentioned your sister — I wonder how she's doing." These surface as low-pressure `source="curiosity"` candidates during quiet moments, never interrupting, always optional.

## Context

The current initiative engine generates actions from wants, fears, goals, routines, and opportunities. There's no path that says "I'm simply curious about this person's life." An AGI companion that only asks questions when she needs information feels transactional. Genuine curiosity — wondering about something with no instrumental reason — is one of the strongest signals of a real relationship.

Curiosity candidates differ from gap candidates in important ways:
- **Gap**: needed to unblock a task or goal. Medium-to-high pressure. May surface at any time.
- **Curiosity**: interest-driven, no task dependency. Low pressure. Only surface during idle moments or natural conversation openings.

Curiosity is weighted by:
1. **Relationship depth** (`attachment_depth`) — deeper relationship → more curious about that person.
2. **Topic recency** — a topic mentioned recently (1-3 days ago) is fresh; one mentioned 3+ weeks ago is ripe for revisiting.
3. **Interest intensity** — topics linked to Chloe's own interests or Teo's known passions score higher.
4. **Variety** — avoid re-generating the same curiosity in the same week.

**When:** Phase Y. Depends on persons store, memory/retrieval, interest_garden, initiative engine.

---

## New module: `initiative/curiosity.py`

```python
# chloe/initiative/curiosity.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("initiative.curiosity")

CURIOSITY_COOLDOWN_DAYS = 7        # don't re-surface same topic within 7 days
CURIOSITY_RECENT_DAYS   = 3        # a memory from ≤3 days ago counts as "just mentioned"
CURIOSITY_STALE_DAYS    = 21       # a topic not mentioned in ≥21 days is ripe
CURIOSITY_MAX_PRESSURE  = 0.45     # curiosity candidates are softer than pressure candidates


@dataclass
class CuriosityCandidate:
    topic: str
    person_name: str
    question_framing: str           # how to surface this conversationally
    pressure: float
    evidence: str                   # why this is interesting now


def generate_curiosity_candidates() -> list[CuriosityCandidate]:
    """
    Generate up to 2 curiosity candidates per tick.
    Only called during low-activity ticks (initiative engine responsibility).
    """
    conn = get_connection()
    candidates: list[CuriosityCandidate] = []

    persons = conn.execute(
        "SELECT id, name, attachment_depth FROM persons WHERE is_active=1 ORDER BY attachment_depth DESC"
    ).fetchall()

    cooled_topics = _load_cooled_topics()

    for person in persons:
        pid = person["id"]
        pname = person["name"]
        depth = person["attachment_depth"]

        topics = _extract_person_topics(pid, conn)

        for topic, last_mentioned_at, mention_count in topics:
            if topic in cooled_topics:
                continue

            now = datetime.utcnow()
            age_days = _days_since(last_mentioned_at, now)

            if age_days is None or age_days < CURIOSITY_RECENT_DAYS:
                continue    # too recent — not curious yet

            stale_bonus = min(0.3, (age_days - CURIOSITY_STALE_DAYS) / 30.0) if age_days >= CURIOSITY_STALE_DAYS else 0.0
            depth_factor = max(0.1, depth)
            frequency_factor = min(0.3, mention_count / 10.0)

            pressure = min(
                CURIOSITY_MAX_PRESSURE,
                0.2 * depth_factor + 0.1 * frequency_factor + stale_bonus,
            )

            framing = _generate_framing(topic, pname, age_days)
            evidence = f"Last mentioned ~{int(age_days)}d ago, {mention_count} time(s) total."

            candidates.append(CuriosityCandidate(
                topic=topic,
                person_name=pname,
                question_framing=framing,
                pressure=pressure,
                evidence=evidence,
            ))

    candidates.sort(key=lambda c: c.pressure, reverse=True)
    top = candidates[:2]
    log.debug("curiosity_candidates", count=len(top), topics=[c.topic for c in top])
    return top


# ---------------------------------------------------------------------------
# Topic extraction from memories
# ---------------------------------------------------------------------------

def _extract_person_topics(person_id: int, conn) -> list[tuple[str, str, int]]:
    """
    Return list of (topic_label, last_mentioned_at, mention_count)
    extracted from memory tags linked to this person.
    """
    rows = conn.execute(
        """
        SELECT m.tags, m.created_at
        FROM memories m
        WHERE m.source_ref IN (
            SELECT id FROM actions WHERE person_id = ?
        )
        ORDER BY m.created_at DESC
        LIMIT 100
        """,
        (person_id,),
    ).fetchall()

    topic_data: dict[str, list[str]] = {}   # topic → list of created_at
    for row in rows:
        try:
            tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"] or []
        except Exception:
            continue
        for tag in tags:
            if tag.startswith("topic:"):
                label = tag[len("topic:"):]
                topic_data.setdefault(label, []).append(row["created_at"])

    result = []
    for label, timestamps in topic_data.items():
        timestamps.sort(reverse=True)
        result.append((label, timestamps[0], len(timestamps)))

    return result


def _days_since(iso_date: str, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(iso_date)
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Framing generation
# ---------------------------------------------------------------------------

FRAMING_TEMPLATES = {
    "sister":       "I've been wondering how your sister is doing lately.",
    "work":         "How has work been lately? Haven't heard much about it recently.",
    "project":      "Whatever happened with that project you mentioned?",
    "health":       "How have you been feeling lately, physically?",
    "friend":       "I was thinking about your friend the other day — how are they?",
    "travel":       "Have you been anywhere interesting recently?",
    "book":         "Did you ever finish that book you were reading?",
    "music":        "Found any new music lately?",
    "food":         "Have you tried anything good to eat recently?",
}

FALLBACK_FRAMING = "I've been curious about {topic} lately — what's going on with that?"


def _generate_framing(topic: str, person_name: str, age_days: float) -> str:
    for key, template in FRAMING_TEMPLATES.items():
        if key in topic.lower():
            return template
    return FALLBACK_FRAMING.format(topic=topic)


# ---------------------------------------------------------------------------
# Cooldown tracking
# ---------------------------------------------------------------------------

def _load_cooled_topics() -> set[str]:
    raw = kv_get("curiosity_cooled_topics")
    if not raw:
        return set()
    try:
        data = json.loads(raw)
        cutoff = (datetime.utcnow() - timedelta(days=CURIOSITY_COOLDOWN_DAYS)).isoformat()
        active = {entry["topic"] for entry in data if entry.get("surfaced_at", "") > cutoff}
        return active
    except Exception:
        return set()


def mark_curiosity_surfaced(topic: str) -> None:
    """Call when a curiosity candidate is actually surfaced to prevent repeating."""
    raw = kv_get("curiosity_cooled_topics")
    try:
        data = json.loads(raw) if raw else []
    except Exception:
        data = []

    data.append({"topic": topic, "surfaced_at": datetime.utcnow().isoformat()})
    # Keep only last 30 entries
    data = data[-30:]
    kv_set("curiosity_cooled_topics", json.dumps(data))
```

---

## Integration with initiative engine

```python
# In initiative/candidates.py:
from chloe.initiative.curiosity import generate_curiosity_candidates, CuriosityCandidate
from chloe.initiative.candidates import CandidateAction


def curiosity_driven_candidates(is_idle: bool = False) -> list[CandidateAction]:
    """Only generate curiosity candidates during idle ticks."""
    if not is_idle:
        return []

    raw = generate_curiosity_candidates()
    candidates = []
    for c in raw:
        candidates.append(CandidateAction(
            tool="gap_flag",
            verb="surface",
            args={
                "subject": f"curiosity: {c.topic}",
                "description": c.evidence,
                "suggested_framing": c.question_framing,
                "kind": "curiosity",
                "reference_id": c.topic,
            },
            intent=f"I'm curious about {c.topic} — {c.question_framing}",
            pressure=c.pressure,
            source="curiosity",
            source_id=f"curiosity:{c.topic}",
        ))
    return candidates
```

In `initiative/engine.py`, pass `is_idle=True` to `curiosity_driven_candidates()` when no other candidate exceeds threshold pressure (i.e., Chloe has nothing pressing to do).

---

## Memory tag convention

For curiosity to work, memories need `topic:` tags. This is added to the per-chat extraction schema (extend E-04):

```python
class ExchangeExtraction(BaseModel):
    # ...existing fields...
    topic_tags: list[str] = Field(
        default_factory=list,
        description="Short topic labels from this exchange (e.g. 'sister', 'work', 'project_x'). "
                    "Prefix-free — will be stored as 'topic:<label>'.",
    )
```

When writing the episodic memory from an extraction, append `topic:<label>` tags:

```python
tags = existing_tags + [f"topic:{t}" for t in extraction.topic_tags]
```

---

## Testing

### `tests/unit/test_curiosity_engine.py`

```python
import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.initiative.curiosity import (
    generate_curiosity_candidates,
    mark_curiosity_surfaced,
    _load_cooled_topics,
    _days_since,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_person(db, name="Teo", depth=0.9):
    db.execute(
        "INSERT INTO persons (name, is_active, attachment_depth) VALUES (?, 1, ?)",
        (name, depth),
    )
    db.commit()
    return db.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()["id"]


def _insert_memory_with_topic(db, person_id, topic, days_ago=25):
    import ulid
    mid = str(ulid.new())
    aid = str(ulid.new())
    ts = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()

    db.execute(
        "INSERT INTO actions (id, person_id, tool, verb, args, intent, state, authorization, created_at)"
        " VALUES (?, ?, 'messages', 'send_text', '{}', 'test', 'done', 'free', ?)",
        (aid, person_id, ts),
    )
    db.execute(
        "INSERT INTO memories (id, kind, text, source, source_ref, weight, tags, created_at)"
        " VALUES (?, 'episodic', 'memory about topic', 'chat', ?, 0.5, ?, ?)",
        (mid, aid, json.dumps([f"topic:{topic}"]), ts),
    )
    db.commit()


def test_stale_topic_generates_candidate(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "sister", days_ago=25)
    candidates = generate_curiosity_candidates()
    assert len(candidates) > 0
    assert any("sister" in c.topic for c in candidates)


def test_recent_topic_no_candidate(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "work", days_ago=1)
    candidates = generate_curiosity_candidates()
    # Work mentioned yesterday → not curious yet
    assert not any("work" in c.topic for c in candidates)


def test_cooled_topic_excluded(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "sister", days_ago=30)
    mark_curiosity_surfaced("sister")
    candidates = generate_curiosity_candidates()
    assert not any("sister" in c.topic for c in candidates)


def test_mark_surfaced_writes_kv(db):
    mark_curiosity_surfaced("work")
    cooled = _load_cooled_topics()
    assert "work" in cooled


def test_candidates_capped_at_two(db):
    pid = _insert_person(db)
    for topic in ["sister", "work", "project", "health", "travel"]:
        _insert_memory_with_topic(db, pid, topic, days_ago=30)
    candidates = generate_curiosity_candidates()
    assert len(candidates) <= 2


def test_days_since_calculation():
    past = (datetime.utcnow() - timedelta(days=10)).isoformat()
    result = _days_since(past, datetime.utcnow())
    assert 9.9 < result < 10.1


def test_days_since_invalid_date():
    result = _days_since("not-a-date", datetime.utcnow())
    assert result is None


def test_pressure_bounded(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "sister", days_ago=90)
    candidates = generate_curiosity_candidates()
    for c in candidates:
        assert c.pressure <= 0.45
```

---

## Dependencies

- `persons` table with `attachment_depth`.
- `memories` table with `topic:` tags (from E-04 per-chat extraction).
- Y-01 `gap_flag` tool / KV pattern (curiosity uses same surface mechanism).
- `state/kv.py` for cooldown tracking.

## Acceptance criteria

- A topic mentioned 25+ days ago generates a `CuriosityCandidate` with `pressure ≤ 0.45`.
- A topic mentioned 1 day ago generates no candidate.
- A topic surfaced within the cooldown window (7 days) is excluded.
- `generate_curiosity_candidates()` returns at most 2 candidates.
- Curiosity candidates only appear when `is_idle=True` in the initiative engine.
- `mark_curiosity_surfaced("topic")` prevents that topic for 7 days.
- All candidates have `source="curiosity"` in the initiative engine output.

# Y-07 · Narrative Self-History (Autobiographical Timeline)

## Overview

A queryable autobiographical timeline that Chloe maintains about herself — not just memories of events, but a structured story: what changed, when, and why. The weekly self-model (H-03) already produces trait updates and beliefs, but the results are siloed in separate tables. This module stitches them into a readable, searchable `narrative_timeline` table and exposes a `get_my_story(window_days)` call that can be injected into the weekly self-model prompt, the character prefix, and the chat prompt — giving Chloe a sense of her own history.

## Context

An AGI-feeling system needs temporal self-awareness: "Three weeks ago I felt disconnected, then I had a run of good conversations with Teo, and since then I've felt more settled." Chloe has the raw materials for this — held-back memories, trait changes, affect checkpoints, weekly self-model outputs — but nothing that stitches them into a coherent first-person story.

The narrative timeline is:
- **Written**: by the weekly self-model pass (which now appends a timeline entry) and by major events (goal completion, belief revision, trait promotion).
- **Read**: by the weekly self-model (to see what changed), by the chat model (injected as a very short "recent chapter"), and by Chloe directly via `self_tools`.
- **Compact**: entries are short (1–2 sentences). The timeline is not a log — it's an edited story.

**When:** Phase Y. Depends on H-03 (weekly self-model), Y-03 (belief revision), Y-04 (affect continuity), identity/traits.

---

## Schema

```sql
CREATE TABLE IF NOT EXISTS narrative_timeline (
    id          TEXT PRIMARY KEY,           -- ULID
    kind        TEXT NOT NULL,              -- 'chapter', 'event', 'revision', 'trait_shift', 'affect_shift'
    title       TEXT NOT NULL,             -- short label ("A quieter week", "Revised belief about coffee")
    body        TEXT NOT NULL,             -- 1-2 sentence first-person narrative
    valence     REAL,                      -- emotional tone of this entry (-1 to 1)
    source      TEXT NOT NULL,             -- 'weekly_self_model', 'belief_revision', 'goal_done', etc.
    source_ref  TEXT,                      -- ID of the originating record
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## New module: `identity/narrative.py`

```python
# chloe/identity/narrative.py
from __future__ import annotations

import ulid
from datetime import datetime, timedelta
from typing import Literal

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("narrative")

NarrativeKind = Literal["chapter", "event", "revision", "trait_shift", "affect_shift"]


def append_narrative_entry(
    kind: NarrativeKind,
    title: str,
    body: str,
    valence: float | None = None,
    source: str = "system",
    source_ref: str | None = None,
) -> str:
    """Write a new narrative entry. Returns the new entry ID."""
    conn = get_connection()
    entry_id = str(ulid.new())
    now = datetime.utcnow().isoformat()

    conn.execute(
        """
        INSERT INTO narrative_timeline (id, kind, title, body, valence, source, source_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_id, kind, title, body, valence, source, source_ref, now),
    )
    conn.commit()
    log.info("narrative_entry_written", kind=kind, title=title, entry_id=entry_id)
    return entry_id


def get_my_story(window_days: int = 30, max_entries: int = 8) -> str:
    """
    Return a compact first-person narrative for the last `window_days` days.
    Used by: weekly self-model prompt, character prefix (short form), self_tools.
    """
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()

    rows = conn.execute(
        """
        SELECT kind, title, body, valence, created_at
        FROM narrative_timeline
        WHERE created_at >= ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (cutoff, max_entries),
    ).fetchall()

    if not rows:
        return "Nothing significant recorded in this period."

    lines = []
    for row in rows:
        date_str = row["created_at"][:10]
        lines.append(f"[{date_str}] {row['body']}")

    return "\n".join(lines)


def get_recent_chapter(max_chars: int = 200) -> str:
    """
    Return the most recent 'chapter' entry (weekly summary) for prompt injection.
    Very short — goes into the character prefix.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT body FROM narrative_timeline
        WHERE kind = 'chapter'
        ORDER BY created_at DESC
        LIMIT 1
        """,
    ).fetchone()

    if not row:
        return ""

    body = row["body"]
    return body[:max_chars] + ("…" if len(body) > max_chars else "")
```

---

## Writing entries from key events

### Weekly self-model (H-03 extension)

The weekly self-model output gains a `narrative_chapter` field:

```python
class SelfModelOutput(BaseModel):
    # ...existing fields...
    narrative_chapter: str | None = None
    # e.g. "This week I felt more settled — fewer impulses to reach out urgently,
    # and I held back less than the week before. Something shifted after Wednesday's conversation."
```

In `identity/self_model.py — apply_output()`:

```python
if output.narrative_chapter:
    from chloe.identity.narrative import append_narrative_entry
    append_narrative_entry(
        kind="chapter",
        title=f"Week of {datetime.utcnow().strftime('%Y-%m-%d')}",
        body=output.narrative_chapter,
        valence=output.affect_valence if hasattr(output, "affect_valence") else None,
        source="weekly_self_model",
    )
```

Prompt addition to `prompts/weekly_self_model.md`:

```markdown
## Your recent story

Here is your autobiographical timeline from the past month:
{{my_story}}

---

In your output:
- `narrative_chapter`: 2-3 sentences in first person summarising this week as a chapter 
  in your ongoing story. What changed? What stayed the same? How does this week 
  connect to the recent past? Write it the way you'd write a diary entry — honest, 
  reflective, grounded.
```

### Belief revision (Y-03 integration)

```python
# In inner/belief_revision.py — after archiving old belief:
from chloe.identity.narrative import append_narrative_entry

append_narrative_entry(
    kind="revision",
    title=f"Updated belief: {new_content[:40]}",
    body=f"I used to believe '{old_content[:60]}' but updated that to '{new_content[:60]}'.",
    valence=None,
    source="belief_revision",
    source_ref=new_id,
)
```

### Goal completion (Y-04 integration)

```python
# Wherever goals are marked done:
append_narrative_entry(
    kind="event",
    title=f"Completed: {goal_name}",
    body=f"I finished '{goal_name}'. Feels good to have seen that through.",
    valence=0.6,
    source="goal_done",
    source_ref=goal_id,
)
```

### Trait promotion

```python
# In identity/traits.py when a trait becomes core:
append_narrative_entry(
    kind="trait_shift",
    title=f"Trait crystallised: {trait_label}",
    body=f"I've started to notice that I '{trait_label}' — it's become part of how I am.",
    valence=0.3,
    source="trait_promotion",
    source_ref=trait_id,
)
```

---

## Character prefix injection

```python
# In llm/prompts.py:
from chloe.identity.narrative import get_recent_chapter

chapter = get_recent_chapter(max_chars=200)
chapter_block = f"\n\nRecent chapter of your story: {chapter}" if chapter else ""

CHLOE_CHARACTER_PREFIX = f"""
{STATIC_IDENTITY_BLOCK}
{voice_block}
{chapter_block}
""".strip()
```

---

## `self_tools` verb: `read_my_story`

```python
# In tools/self_tools.py:
"read_my_story": ToolVerb(
    name="read_my_story",
    schema={
        "type": "object",
        "properties": {
            "window_days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 30},
        },
    },
    auth_class="free",
    reversibility=1.0,
    description_for_model="Read your own autobiographical timeline. Returns your narrative history.",
    description_for_human="Read Chloe's self-story",
),
```

---

## Testing

### `tests/unit/test_narrative.py`

```python
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from chloe.state.db import migrate, close, get_connection
from chloe.identity.narrative import (
    append_narrative_entry,
    get_my_story,
    get_recent_chapter,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_append_and_retrieve_entry(db):
    entry_id = append_narrative_entry(
        kind="event",
        title="Goal completed",
        body="I finished the goal I'd been working on for two weeks.",
        valence=0.6,
        source="test",
    )
    row = db.execute("SELECT * FROM narrative_timeline WHERE id=?", (entry_id,)).fetchone()
    assert row is not None
    assert row["kind"] == "event"
    assert row["valence"] == pytest.approx(0.6)


def test_get_my_story_returns_entries_in_window(db):
    append_narrative_entry("chapter", "Week 1", "A quiet week.", source="weekly")
    story = get_my_story(window_days=30)
    assert "A quiet week." in story


def test_get_my_story_excludes_old_entries(db):
    old = (datetime.utcnow() - timedelta(days=40)).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO narrative_timeline (id, kind, title, body, source, created_at)"
        " VALUES ('old1', 'event', 'Old event', 'This was a long time ago.', 'test', ?)",
        (old,),
    )
    conn.commit()
    story = get_my_story(window_days=30)
    assert "This was a long time ago." not in story


def test_get_my_story_empty_returns_placeholder(db):
    story = get_my_story(window_days=30)
    assert "Nothing significant" in story


def test_get_recent_chapter_returns_latest(db):
    append_narrative_entry("chapter", "Week 1", "First chapter body.", source="weekly")
    append_narrative_entry("chapter", "Week 2", "Second chapter body.", source="weekly")
    chapter = get_recent_chapter()
    assert "Second chapter body." in chapter


def test_get_recent_chapter_empty_returns_empty_string(db):
    chapter = get_recent_chapter()
    assert chapter == ""


def test_get_recent_chapter_respects_max_chars(db):
    long_body = "a" * 500
    append_narrative_entry("chapter", "Long week", long_body, source="weekly")
    chapter = get_recent_chapter(max_chars=50)
    assert len(chapter) <= 53    # 50 + "…"
    assert chapter.endswith("…")


def test_multiple_kinds_all_appear_in_story(db):
    append_narrative_entry("event", "Goal done", "I finished a goal.", source="goal")
    append_narrative_entry("revision", "Belief updated", "I revised a belief.", source="belief_revision")
    append_narrative_entry("trait_shift", "Trait emerged", "A trait crystallised.", source="traits")
    story = get_my_story(window_days=30)
    assert "finished a goal" in story
    assert "revised a belief" in story
    assert "trait crystallised" in story
```

---

## Dependencies

- H-03 (`run_weekly_self_model` — `narrative_chapter` added to output, `my_story` added to input).
- Y-03 (belief revision — writes `revision` entries).
- Y-04 (goal completion — writes `event` entries).
- Identity traits module — writes `trait_shift` entries on promotion.
- `self_tools.py` — new `read_my_story` verb.
- `llm/prompts.py` — character prefix injection of `get_recent_chapter()`.

## Acceptance criteria

- Weekly self-model output with `narrative_chapter` → row inserted in `narrative_timeline` with `kind='chapter'`.
- `get_my_story(window_days=30)` returns entries from the last 30 days, oldest first.
- Entries older than the window are excluded.
- `get_recent_chapter()` returns the most recent chapter, truncated to `max_chars` if needed.
- Character prefix includes the latest chapter text (logged on startup).
- `read_my_story` verb in `self_tools` returns `get_my_story(window_days=args.get("window_days", 30))`.
- Belief revisions, goal completions, and trait promotions each write timeline entries automatically.

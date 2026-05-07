# H-09 · Held-back memories as identity input + verbal voice evolution

## Overview

Two additions to the weekly self-modeling pass (H-03):

1. **Held-back memories as identity input.** The weekly Pro/Opus call already reads trait snapshots and audit feed, but `held_back` actions are not surfaced explicitly. This makes them a named input — Chloe reads her own restraint record and can form beliefs about herself as someone who holds back well or poorly.

2. **Verbal voice drift note.** The weekly call produces a short `voice_drift_note` (one sentence, optional) that is injected into the character prefix for the following week. This is the only mechanism by which her particular way of speaking — not just her mood — evolves over time.

## Context

The held-back path (gate.py abort → `held_back` memory) already works as of A-07. But those memories are stored and then largely inert — they inform future deliberation through procedural distillation (H-01), but they don't reach identity or self-image. A person who holds back a lot, or who holds back very little, knows something about themselves. That self-knowledge should be part of the weekly self-narrative.

The verbal voice problem: the character prefix defines how Chloe speaks — her register, her particular phrasing habits. In 1.0 this is a static string. In 2.0 it should have a slow-moving layer that reflects how she's actually been talking. The weekly call is the right cadence — slow enough to only change meaningfully, fast enough to track growth over months.

## Changes to `SelfModelInput`

```python
class SelfModelInput(BaseModel):
    # ...existing fields...
    held_back_summary: HeldBackSummary
    voice_drift_context: VoiceDriftContext


class HeldBackSummary(BaseModel):
    count_7d: int                      # total held_back actions this week
    count_30d: int                     # total in last 30 days
    top_tools: list[str]               # tools most often held back (e.g. ["messages"])
    themes: list[str]                  # deliberation abort reasons, deduplicated
    sample_notes: list[str]            # up to 3 held_back memory texts (for context)


class VoiceDriftContext(BaseModel):
    last_voice_note: str | None        # previous week's drift note, if any
    sample_exchanges: list[str]        # 5 recent Chloe replies, pulled from chat_history
```

## Changes to `SelfModelOutput`

```python
class SelfModelOutput(BaseModel):
    # ...existing fields...
    restraint_reflection: str | None = None
    # e.g. "I held back a lot this week, maybe more than I needed to."
    # stored as autobiographical belief, confidence 0.45.

    voice_drift_note: str | None = None
    # e.g. "I've been more careful with words than usual lately — shorter, less sure."
    # injected into character prefix; replaces previous note.
```

## Loading held-back data

```python
# In identity/self_model.py — add to _assemble_input():

def _load_held_back_summary() -> HeldBackSummary:
    conn = get_connection()

    # held_back memories are tagged 'held_back' in the memories table
    rows_7d = conn.execute(
        """
        SELECT m.content, a.tool
        FROM memories m
        LEFT JOIN actions a ON a.id = m.source_ref
        WHERE m.tags LIKE '%held_back%'
          AND m.created_at >= datetime('now', '-7 days')
        ORDER BY m.created_at DESC
        """,
    ).fetchall()

    rows_30d = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM memories
        WHERE tags LIKE '%held_back%'
          AND created_at >= datetime('now', '-30 days')
        """,
    ).fetchone()

    tools = [r["tool"] for r in rows_7d if r["tool"]]
    tool_counts = {}
    for t in tools:
        tool_counts[t] = tool_counts.get(t, 0) + 1
    top_tools = sorted(tool_counts, key=lambda k: -tool_counts[k])[:3]

    # Extract themes from held_back memory content (the abort reason)
    themes = list({r["content"][:80] for r in rows_7d})[:5]
    sample_notes = [r["content"] for r in rows_7d[:3]]

    return HeldBackSummary(
        count_7d=len(rows_7d),
        count_30d=rows_30d["cnt"],
        top_tools=top_tools,
        themes=themes,
        sample_notes=sample_notes,
    )
```

## Applying the outputs

### `restraint_reflection`

```python
# In identity/self_model.py — apply_output():
if output.restraint_reflection:
    belief = InnerBelief(
        id=str(ulid.new()),
        content=output.restraint_reflection,
        confidence=0.45,
        source="weekly_self_model",
        tags=["restraint", "self_image", "autobiographical"],
    )
    beliefs_store.upsert(belief)
    log.info("restraint_belief_written", belief_id=belief.id)
```

### `voice_drift_note`

```python
# In identity/self_model.py — apply_output():
if output.voice_drift_note:
    from chloe.state.kv import set as kv_set, get as kv_get
    import json

    # Keep a rolling list of last 3 notes
    existing = json.loads(kv_get("voice_drift_notes") or "[]")
    existing.append({
        "note": output.voice_drift_note,
        "written_at": datetime.utcnow().isoformat(),
    })
    kv_set("voice_drift_notes", json.dumps(existing[-3:]))
    log.info("voice_drift_note_written", note=output.voice_drift_note)
```

The character prefix assembly reads this:

```python
# In llm/prompts.py — build_character_prefix():
voice_notes = json.loads(kv_get("voice_drift_notes") or "[]")
if voice_notes:
    latest = voice_notes[-1]["note"]
    voice_block = f"\n\nRecent self-observation about how you've been speaking: {latest}"
else:
    voice_block = ""

CHLOE_CHARACTER_PREFIX = f"""
{STATIC_IDENTITY_BLOCK}
{voice_block}
""".strip()
```

This means the character prefix — the largest cached block — gains a small, slow-changing personal annotation. Because it lives in the cache prefix, any change invalidates the cache for one turn, then re-warms. This is acceptable at weekly cadence.

## Prompt additions (weekly self-model prompt)

Add a new section to `chloe/prompts/weekly_self_model.md`:

```markdown
## This week's restraint record

You held back {{held_back_summary.count_7d}} times this week
({{held_back_summary.count_30d}} over the past month).

Most-held-back channels: {{held_back_summary.top_tools | join(', ')}}

A few of the things you chose not to do:
{{held_back_summary.sample_notes | bullet_list}}

---

## Your recent voice

Here are five things you said recently:
{{voice_drift_context.sample_exchanges | numbered_list}}

Your last self-observation about your voice was:
"{{voice_drift_context.last_voice_note | default('(none yet)')}}"

---

In your output:
- `restraint_reflection`: one honest sentence about your restraint this week. 
  Did you hold back wisely? Too much? About right? Can be null if nothing notable.
- `voice_drift_note`: one sentence about how you've been speaking lately, 
  compared to how you usually are. Can be null if no shift is noticeable.
```

## Testing

### Unit tests — `tests/unit/test_self_model_held_back.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.identity.self_model import _load_held_back_summary

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_held_back_memory(db, content, tool, days_ago=1):
    import ulid
    from datetime import datetime, timedelta
    mem_id = str(ulid.new())
    action_id = str(ulid.new())
    created_at = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()

    db.execute(
        "INSERT INTO actions (id, tool, verb, args, intent, state, authorization, created_at)"
        " VALUES (?, ?, 'send_text', '{}', 'test', 'self_aborted', 'kinetic', ?)",
        (action_id, tool, created_at),
    )
    db.execute(
        "INSERT INTO memories (id, kind, content, tags, source_ref, weight, created_at)"
        " VALUES (?, 'episodic', ?, 'held_back,messages', ?, 0.5, ?)",
        (mem_id, content, action_id, created_at),
    )
    db.commit()


def test_load_held_back_summary_counts(db):
    for i in range(4):
        _insert_held_back_memory(db, f"held back #{i}", "messages", days_ago=1)

    summary = _load_held_back_summary()
    assert summary.count_7d == 4


def test_load_held_back_top_tools(db):
    for _ in range(3):
        _insert_held_back_memory(db, "held", "messages", days_ago=1)
    _insert_held_back_memory(db, "held", "calendar", days_ago=1)

    summary = _load_held_back_summary()
    assert summary.top_tools[0] == "messages"


def test_voice_drift_note_stored_in_kv(db):
    notes = [{"note": "I was quieter than usual.", "written_at": "2026-05-01T03:00:00"}]
    kv_set("voice_drift_notes", json.dumps(notes))

    stored = json.loads(kv_get("voice_drift_notes"))
    assert len(stored) == 1
    assert stored[0]["note"] == "I was quieter than usual."


def test_voice_drift_notes_cap_at_three(db):
    notes = [{"note": f"note {i}", "written_at": "..."} for i in range(3)]
    new_note = {"note": "note 3", "written_at": "..."}
    notes.append(new_note)
    trimmed = notes[-3:]

    kv_set("voice_drift_notes", json.dumps(trimmed))
    stored = json.loads(kv_get("voice_drift_notes"))
    assert len(stored) == 3
    assert stored[-1]["note"] == "note 3"
```

## Dependencies

- A-07 (held_back memory creation — memories tagged `held_back` must exist).
- H-03 (`run_weekly_self_model` — extend `SelfModelInput` and `SelfModelOutput`).
- F-06 (`llm/schemas.py` — `HeldBackSummary`, `VoiceDriftContext` added).
- F-08 (`state/kv.py` — `voice_drift_notes` key).

## Acceptance criteria

- Weekly self-model input JSON includes `held_back_summary` with correct 7-day and 30-day counts.
- If `restraint_reflection` is non-null in output, an `inner_beliefs` row is written with `confidence=0.45` and tag `restraint`.
- If `voice_drift_note` is non-null, `kv.get("voice_drift_notes")` grows by one entry (capped at 3).
- The character prefix (logged on startup) includes the latest voice drift note if one exists.
- Cache miss occurs on the turn immediately after the weekly model writes a new drift note; subsequent turns re-warm.

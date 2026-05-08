# Y-03 · Belief Revision Engine

## Overview

When Chloe receives new information that contradicts an existing belief, she explicitly revises it: the old belief is archived with a `superseded_by` link, the new belief is written with a higher-confidence, and a short revision memory is created. This makes her feel like she has a living world model rather than a write-only profile — she knows what she used to think, knows she changed her mind, and can say so.

## Context

The current belief store (`inner_beliefs`) supports upsert but not revision tracking. A belief about Teo's coffee preference can be silently overwritten. From Chloe's perspective, nothing happened — there's no record that she previously believed otherwise, no acknowledgement that she updated her view, no epistemic continuity.

Belief revision changes this:
1. Before writing a new belief, check for semantic overlap with existing beliefs on the same topic.
2. If an overlapping belief exists and the new content contradicts it (confidence delta > threshold), archive the old one with `superseded_by=<new_id>`.
3. Write a short "I updated my understanding of X" memory.
4. The model can surface this: "I thought you preferred espresso, but you mentioned americanos today — I've updated that."

This is also the foundation for **epistemic honesty**: Chloe can say "I'm not sure about that" when a belief has low confidence, and "I used to think X but updated that" when a revision exists.

**When:** Phase Y. Depends on inner beliefs store, memory/store.

---

## Schema additions

```sql
-- New columns on inner_beliefs:
ALTER TABLE inner_beliefs ADD COLUMN superseded_by TEXT;       -- ULID of the replacing belief
ALTER TABLE inner_beliefs ADD COLUMN supersedes    TEXT;       -- ULID of the belief this replaced
ALTER TABLE inner_beliefs ADD COLUMN revision_note TEXT;       -- one-line reason for revision
ALTER TABLE inner_beliefs ADD COLUMN archived      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inner_beliefs ADD COLUMN updated_at    TEXT    NOT NULL DEFAULT (datetime('now'));
```

---

## New module: `inner/belief_revision.py`

```python
# chloe/inner/belief_revision.py
from __future__ import annotations

import ulid
from datetime import datetime
from typing import TYPE_CHECKING

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

if TYPE_CHECKING:
    pass

log = get_logger("belief_revision")

REVISION_CONFIDENCE_DELTA = 0.25    # minimum gap before treating as a contradiction
OVERLAP_TAG_THRESHOLD = 2           # shared tags needed to consider beliefs "on same topic"


def upsert_belief_with_revision(
    content: str,
    confidence: float,
    source: str,
    tags: list[str],
    revision_note: str | None = None,
) -> str:
    """
    Insert a new belief. If a semantically overlapping belief already exists
    and confidence differs enough, archive the old one and link them.
    Returns the new belief's ID.
    """
    conn = get_connection()
    new_id = str(ulid.new())
    now = datetime.utcnow().isoformat()

    existing = _find_overlapping_belief(tags, conn)

    if existing and _is_contradiction(existing["confidence"], confidence):
        old_id = existing["id"]

        # Archive old belief
        conn.execute(
            """
            UPDATE inner_beliefs
            SET archived = 1,
                superseded_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (new_id, now, old_id),
        )

        # Write revision memory
        _write_revision_memory(
            old_content=existing["content"],
            new_content=content,
            note=revision_note or f"Updated after new information from {source}.",
        )

        log.info("belief_revised", old_id=old_id, new_id=new_id, source=source)
        supersedes = old_id
    else:
        supersedes = None

    import json
    conn.execute(
        """
        INSERT INTO inner_beliefs
            (id, content, confidence, source, tags, supersedes, archived, updated_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            new_id,
            content,
            confidence,
            source,
            json.dumps(tags),
            supersedes,
            now,
            now,
        ),
    )
    conn.commit()
    return new_id


def _find_overlapping_belief(tags: list[str], conn) -> dict | None:
    """Find the highest-confidence active belief that shares enough tags."""
    if not tags:
        return None

    rows = conn.execute(
        "SELECT id, content, confidence, tags FROM inner_beliefs WHERE archived = 0 ORDER BY confidence DESC"
    ).fetchall()

    import json
    for row in rows:
        try:
            existing_tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"] or []
        except Exception:
            existing_tags = []

        overlap = len(set(tags) & set(existing_tags))
        if overlap >= OVERLAP_TAG_THRESHOLD:
            return dict(row)

    return None


def _is_contradiction(old_confidence: float, new_confidence: float) -> bool:
    return abs(new_confidence - old_confidence) >= REVISION_CONFIDENCE_DELTA


def _write_revision_memory(old_content: str, new_content: str, note: str) -> None:
    from chloe.memory.store import MemoryStore
    import ulid as _ulid
    store = MemoryStore()

    mem_id = str(_ulid.new())
    # Write synchronously — revision memories are small and urgent
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO memories (id, kind, text, source, weight, tags, created_at)
        VALUES (?, 'autobiographical', ?, 'belief_revision', 0.7,
                '["belief_revision","autobiographical"]', datetime('now'))
        """,
        (
            mem_id,
            f"I updated my understanding: I used to believe '{old_content[:80]}' "
            f"but now believe '{new_content[:80]}'. {note}",
        ),
    )
    conn.commit()
    log.info("revision_memory_written", mem_id=mem_id)


# ---------------------------------------------------------------------------
# Epistemic confidence helper
# ---------------------------------------------------------------------------

def get_belief_confidence_summary(tags: list[str]) -> dict | None:
    """
    Return a confidence summary for beliefs matching the given tags.
    Used by the chat model to express appropriate uncertainty.
    """
    conn = get_connection()
    import json

    rows = conn.execute(
        "SELECT content, confidence FROM inner_beliefs WHERE archived = 0"
    ).fetchall()

    matches = []
    for row in rows:
        try:
            btags = json.loads(row["tags"]) if isinstance(row["tags"], str) else []
        except Exception:
            btags = []
        if len(set(tags) & set(btags)) >= 1:
            matches.append(dict(row))

    if not matches:
        return None

    avg_conf = sum(m["confidence"] for m in matches) / len(matches)
    return {
        "count": len(matches),
        "avg_confidence": round(avg_conf, 2),
        "uncertain": avg_conf < 0.5,
        "top_belief": max(matches, key=lambda m: m["confidence"])["content"],
    }
```

---

## Integration

Replace direct `inner_beliefs` inserts throughout the codebase with `upsert_belief_with_revision`:

```python
# Before (in identity/self_model.py, reflect/extract.py, etc.):
conn.execute(
    "INSERT INTO inner_beliefs (content, confidence, source, tags) VALUES (?, ?, ?, ?)",
    (content, confidence, source, json.dumps(tags)),
)

# After:
from chloe.inner.belief_revision import upsert_belief_with_revision
upsert_belief_with_revision(
    content=content,
    confidence=confidence,
    source=source,
    tags=tags,
    revision_note="Observed during conversation.",
)
```

### Epistemic injection in chat prompt

When the model is about to discuss a topic related to a low-confidence belief, inject a soft note:

```python
# In llm/prompts.py:
from chloe.inner.belief_revision import get_belief_confidence_summary

def _epistemic_note(intent: str, tags: list[str]) -> str:
    summary = get_belief_confidence_summary(tags)
    if summary and summary["uncertain"]:
        return (
            f"\n\n[Epistemic note: Your belief about this topic "
            f"('{summary['top_belief'][:60]}') has avg confidence "
            f"{summary['avg_confidence']:.2f}. Express appropriate uncertainty.]"
        )
    return ""
```

---

## Testing

### `tests/unit/test_belief_revision.py`

```python
import pytest
import json
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.inner.belief_revision import (
    upsert_belief_with_revision,
    _find_overlapping_belief,
    _is_contradiction,
    get_belief_confidence_summary,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_first_belief_inserted_without_revision(db):
    new_id = upsert_belief_with_revision(
        content="Teo prefers espresso",
        confidence=0.8,
        source="chat",
        tags=["teo", "coffee", "preference"],
    )
    row = db.execute("SELECT * FROM inner_beliefs WHERE id=?", (new_id,)).fetchone()
    assert row is not None
    assert row["archived"] == 0
    assert row["supersedes"] is None


def test_contradicting_belief_archives_old(db):
    old_id = upsert_belief_with_revision(
        content="Teo prefers espresso",
        confidence=0.8,
        source="chat",
        tags=["teo", "coffee", "preference"],
    )
    new_id = upsert_belief_with_revision(
        content="Teo prefers americano",
        confidence=0.85,
        source="chat",
        tags=["teo", "coffee", "preference"],
    )
    old_row = db.execute("SELECT * FROM inner_beliefs WHERE id=?", (old_id,)).fetchone()
    new_row = db.execute("SELECT * FROM inner_beliefs WHERE id=?", (new_id,)).fetchone()

    assert old_row["archived"] == 1
    assert old_row["superseded_by"] == new_id
    assert new_row["supersedes"] == old_id


def test_revision_creates_autobiographical_memory(db):
    upsert_belief_with_revision("Teo likes tea", 0.8, "chat", ["teo", "tea", "preference"])
    upsert_belief_with_revision("Teo dislikes tea", 0.85, "chat", ["teo", "tea", "preference"])

    mem = db.execute(
        "SELECT * FROM memories WHERE source='belief_revision'"
    ).fetchone()
    assert mem is not None
    assert "updated my understanding" in mem["text"]


def test_similar_confidence_no_revision(db):
    upsert_belief_with_revision("Teo likes espresso", 0.7, "chat", ["teo", "espresso", "preference"])
    upsert_belief_with_revision("Teo likes espresso in the morning", 0.72, "chat", ["teo", "espresso", "preference"])

    archived = db.execute(
        "SELECT COUNT(*) as cnt FROM inner_beliefs WHERE archived=1"
    ).fetchone()["cnt"]
    assert archived == 0


def test_is_contradiction_threshold():
    assert _is_contradiction(0.8, 0.3) is True
    assert _is_contradiction(0.7, 0.72) is False


def test_epistemic_summary_uncertain_flag(db):
    upsert_belief_with_revision("maybe Teo likes jazz", 0.3, "inference", ["teo", "music", "jazz"])
    summary = get_belief_confidence_summary(["teo", "music"])
    assert summary is not None
    assert summary["uncertain"] is True


def test_epistemic_summary_confident_flag(db):
    upsert_belief_with_revision("Teo loves Italian food", 0.9, "direct", ["teo", "food", "italian"])
    summary = get_belief_confidence_summary(["teo", "food"])
    assert summary is not None
    assert summary["uncertain"] is False
```

---

## Dependencies

- `inner_beliefs` table with new columns (`superseded_by`, `supersedes`, `revision_note`, `updated_at`).
- `memories` table (revision memory write).
- Inner beliefs writers in `identity/self_model.py`, `reflect/extract.py` (call site swap).

## Acceptance criteria

- New belief with overlapping tags (≥2) and confidence delta ≥0.25 → old belief archived with `superseded_by` set.
- Revision creates a `kind='autobiographical'` memory with `source='belief_revision'`.
- Same tags, confidence delta <0.25 → no archival (beliefs coexist).
- `get_belief_confidence_summary(tags)` returns `uncertain=True` when avg confidence <0.5.
- `upsert_belief_with_revision` is idempotent: calling with identical content does not create a revision.

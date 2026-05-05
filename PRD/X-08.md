# X-08 · Humor as seeded emergent trait + inside-joke memory

## Overview

The trait-emergence system is correct but has no vocabulary for humor — it would need dozens of exchanges with explicit comedic content before any humor-related trait could organically crystallize. This step seeds the system with an early-detection path for humor so it can emerge faster and more precisely, and introduces **inside jokes** as a special-case semantic memory that accumulates over time and gets retrieval-priority bonuses when relevant context appears.

## Context

Humor is one of the most defining dimensions of a relationship personality. A shared sense of humor — or a mismatch in one — shapes everything: register, tone, when she's playful, what she avoids. The current trait system would eventually grow something like "tends toward dry observation" if given enough signal, but the per-chat extraction schema gives Haiku no field to flag humor specifically, so the signal barely reaches the trait store.

This does not add hardcoded humor traits. It adds:
1. A detection field in per-chat extraction so Haiku can flag humorous exchanges.
2. A seeding path that, when detection fires repeatedly early, crystallizes the correct humor-adjacent trait faster.
3. An inside-joke memory kind (a specialized semantic memory) that creates a persistent shared reference and retrieves with a bonus when the same topic recurs.

**When:** Phase E (alongside trait system work in E-07/E-08).

## Per-chat extraction changes

Add to `ExchangeExtraction` (extend E-04 schema):

```python
class HumorDetection(BaseModel):
    detected: bool = False
    kind: Literal["dry", "warm", "playful", "dark", "absurdist"] | None = None
    direction: Literal["teo_to_chloe", "chloe_to_teo", "mutual"] | None = None
    inside_joke_candidate: bool = False
    inside_joke_topic: str | None = None   # one-word/short topic label if candidate


class ExchangeExtraction(BaseModel):
    # ...existing fields...
    humor: HumorDetection = Field(default_factory=HumorDetection)
```

Haiku extraction prompt gains a section:

```markdown
## Humor

- `humor.detected`: was there a genuinely funny or playful moment in this exchange?
  Be conservative — a wry comment counts; generic politeness does not.
- `humor.kind`: dry (understated, ironic), warm (gentle, affectionate), 
  playful (silly, teasing), dark (morbid or edgy), absurdist (surreal logic).
- `humor.direction`: who initiated the humor? Or was it mutual?
- `humor.inside_joke_candidate`: true if this humor references something 
  the two of them have talked about before, or if it's the second+ time 
  this specific joke/image has appeared.
- `humor.inside_joke_topic`: short label if candidate (e.g. "the whale thing", "3am brain").
```

## Humor seeding path

```python
# chloe/identity/traits.py — extend existing trait management

HUMOR_SEED_THRESHOLD = 4          # detections before seeding a candidate trait
HUMOR_SEED_WINDOW_DAYS = 14       # within this window

HUMOR_KIND_TO_TRAIT = {
    "dry":        "finds dry wit charming",
    "warm":       "responds to warmth with warmth",
    "playful":    "enjoys being teased and teasing back",
    "dark":       "comfortable with dark humor",
    "absurdist":  "delights in absurdist tangents",
}


def record_humor_detection(kind: str | None, direction: str | None) -> None:
    """
    Called after extraction. Increments humor detection counter per kind.
    When threshold is reached, seeds a candidate trait if none exists.
    """
    if kind is None:
        return

    from chloe.state.kv import get as kv_get, set as kv_set
    import json
    from datetime import datetime, timedelta

    key = f"humor_detections_{kind}"
    records = json.loads(kv_get(key) or "[]")
    now = datetime.utcnow().isoformat()
    cutoff = (datetime.utcnow() - timedelta(days=HUMOR_SEED_WINDOW_DAYS)).isoformat()

    records.append(now)
    records = [r for r in records if r >= cutoff]
    kv_set(key, json.dumps(records))

    if len(records) >= HUMOR_SEED_THRESHOLD:
        _seed_humor_trait_if_absent(kind)


def _seed_humor_trait_if_absent(kind: str) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()

    trait_label = HUMOR_KIND_TO_TRAIT.get(kind)
    if trait_label is None:
        return

    existing = conn.execute(
        "SELECT id FROM identity_traits WHERE label = ?", (trait_label,)
    ).fetchone()
    if existing:
        return

    import ulid
    conn.execute(
        """
        INSERT INTO identity_traits (id, label, weight, is_core, source, created_at)
        VALUES (?, ?, 0.3, 0, 'humor_seed', datetime('now'))
        """,
        (str(ulid.new()), trait_label),
    )
    conn.commit()
    from chloe.observability.logging import get_logger
    get_logger("traits").info("humor_trait_seeded", kind=kind, label=trait_label)
```

The seeded trait starts at `weight=0.3` — not core, subject to normal decay if humor never appears again, promoted to core by the regular trait-promotion cycle if reinforced.

## Inside-joke memories

An inside joke is a specialized `semantic` memory created when `humor.inside_joke_candidate=True`.

### `memory/inside_jokes.py`

```python
# chloe/memory/inside_jokes.py
from __future__ import annotations
from chloe.memory.store import MemoryStore
from chloe.memory.models import Memory
from chloe.observability.logging import get_logger
import ulid

log = get_logger("inside_jokes")
_store = MemoryStore()

INSIDE_JOKE_WEIGHT = 0.85          # high — these are important
RETRIEVAL_BONUS = 0.12             # added to score when topic matches


async def record_inside_joke(topic: str, context_snippet: str) -> str | None:
    """
    Create or reinforce an inside-joke memory for the given topic.
    Returns memory_id if created, None if reinforced.
    """
    existing = await _find_existing(topic)

    if existing:
        # Reinforce: bump weight, add to content
        new_weight = min(1.0, existing["weight"] + 0.05)
        await _store.set_weight(existing["id"], new_weight)
        log.info("inside_joke_reinforced", topic=topic, memory_id=existing["id"])
        return None

    memory = Memory(
        id=str(ulid.new()),
        kind="semantic",
        content=f"Inside reference with Teo: '{topic}'. ({context_snippet[:120]})",
        tags=["inside_joke", "semantic", f"joke_topic:{topic}"],
        weight=INSIDE_JOKE_WEIGHT,
        source="humor_detection",
        artifact_refs=[],
    )
    await _store.upsert(memory)
    log.info("inside_joke_created", topic=topic, memory_id=memory.id)
    return memory.id


async def _find_existing(topic: str) -> dict | None:
    from chloe.state.db import get_connection
    conn = get_connection()
    return conn.execute(
        "SELECT id, weight FROM memories WHERE tags LIKE ? AND kind='semantic'",
        (f"%joke_topic:{topic}%",),
    ).fetchone()
```

### Retrieval bonus

In `memory/retrieval.py`, after the grader pass, apply a bonus to inside-joke memories when the incoming query overlaps their topic tag:

```python
def _apply_inside_joke_bonus(candidates: list[Memory], query: str) -> list[Memory]:
    for m in candidates:
        for tag in m.tags:
            if tag.startswith("joke_topic:"):
                topic = tag.removeprefix("joke_topic:")
                if topic.lower() in query.lower():
                    m.retrieval_score = getattr(m, "retrieval_score", 0.5) + RETRIEVAL_BONUS
    return candidates
```

This ensures that when Teo says anything that echoes a past joke, the memory surfaces naturally in the chat context — Chloe "remembers" the bit.

## Testing

### Unit tests — `tests/unit/test_humor.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.identity.traits import record_humor_detection, _seed_humor_trait_if_absent

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_humor_detection_under_threshold_no_trait(db):
    for _ in range(3):    # threshold is 4
        record_humor_detection("dry", "mutual")
    trait = db.execute(
        "SELECT id FROM identity_traits WHERE label='finds dry wit charming'"
    ).fetchone()
    assert trait is None


def test_humor_detection_at_threshold_seeds_trait(db):
    for _ in range(4):
        record_humor_detection("dry", "mutual")
    trait = db.execute(
        "SELECT weight FROM identity_traits WHERE label='finds dry wit charming'"
    ).fetchone()
    assert trait is not None
    assert trait["weight"] == pytest.approx(0.3)


def test_humor_seed_does_not_duplicate(db):
    for _ in range(8):
        record_humor_detection("dry", "mutual")
    count = db.execute(
        "SELECT COUNT(*) as cnt FROM identity_traits WHERE label='finds dry wit charming'"
    ).fetchone()["cnt"]
    assert count == 1


def test_humor_none_kind_is_noop(db):
    record_humor_detection(None, None)  # should not raise or write anything


@pytest.mark.asyncio
async def test_inside_joke_created_on_first_candidate():
    with patch("chloe.memory.inside_jokes._store") as mock_store:
        mock_store.upsert = AsyncMock()
        mock_store.set_weight = AsyncMock()
        result = await record_inside_joke("the whale thing", "Teo said 'kitchen whales again'")
    assert result is not None


@pytest.mark.asyncio
async def test_inside_joke_reinforced_on_repeat():
    with patch("chloe.memory.inside_jokes._find_existing") as mock_find:
        mock_find.return_value = {"id": "existing_id", "weight": 0.85}
        with patch("chloe.memory.inside_jokes._store") as mock_store:
            mock_store.set_weight = AsyncMock()
            result = await record_inside_joke("the whale thing", "again")
    assert result is None    # reinforced, not created anew
```

## Dependencies

- E-04 (per-chat extraction schema — `humor` field added).
- E-07/E-08 (trait emergence cycle — seeded traits enter the normal cycle).
- E-06 (memory retrieval — `_apply_inside_joke_bonus` added to retrieval pipeline).
- F-06 (`llm/schemas.py` — `HumorDetection` model).

## Acceptance criteria

- 4 exchanges with `humor.detected=True, humor.kind="dry"` within 14 days → `identity_traits` gains a row with `label="finds dry wit charming"`, `weight=0.3`.
- Seeding is idempotent: 8 such exchanges → still only 1 trait row.
- `humor.inside_joke_candidate=True` with a topic → a semantic memory is created tagged `joke_topic:<topic>`.
- Second detection of the same topic → memory weight increases, no duplicate created.
- In a simulated retrieval, the inside-joke memory scores higher when the query contains the topic word.

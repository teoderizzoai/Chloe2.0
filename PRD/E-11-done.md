# E-11 · `persons.attachment_depth` — relational depth as first-class state

## Overview

Add `attachment_depth REAL DEFAULT 0.0` (range `[-1.0, 1.0]`) to the `persons` table. It drifts up with shared positive episodics and direct warmth, and decays with prolonged silence or detected coldness. It is read by the affect model, the initiative engine, the chat prompt, and the weekly self-model. It makes the relationship's actual history shape Chloe's openness and initiative thresholds — not just her current mood.

## Context

1.0 has an `attachment` table per person (`anxious_attachment`, `avoidant_attachment` floats) but it rarely surfaces in behavior. The problem with the current design is that openness and social pull are functions of *mood*, not of *relationship history*. A Chloe who has been talking to Teo every day for three months should be more open with him than one who met him last week — regardless of what her current valence is. `attachment_depth` is the slow-moving relational variable that captures this.

It belongs in Phase E because it's a per-person state scalar (analogous to the `kv` migration) and it feeds the same affect model being refactored in E-03/E-04.

## Schema change

```sql
-- Add to persons table (migration E-11-001.sql)
ALTER TABLE persons ADD COLUMN attachment_depth REAL NOT NULL DEFAULT 0.0;
```

Range semantics:
- `< -0.3` — estranged; recent rupture unrepaired or sustained coldness
- `-0.3..0.3` — neutral / early relationship
- `0.3..0.7` — familiar; comfortable
- `> 0.7` — deep; she is more open, more likely to initiate, less guarded

## Update path

### Per-chat extraction (extend E-04 extraction schema)

Add one field to the combined extraction output:

```python
class ExchangeExtraction(BaseModel):
    # ...existing fields...
    attachment_delta: float = Field(
        ge=-0.05, le=0.05, default=0.0,
        description=(
            "Small signed nudge to attachment_depth for this person. "
            "Positive: warm, connected, mutually engaged. "
            "Negative: dismissive, absent, cold. "
            "Zero: neutral or purely informational exchange."
        )
    )
```

### `persons/attachment.py`

```python
# chloe/persons/attachment.py
from __future__ import annotations
import math
from datetime import datetime, timedelta
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("attachment")

DEPTH_MIN = -1.0
DEPTH_MAX = 1.0
SILENCE_DECAY_PER_DAY = 0.01    # slow drift toward neutral when absent
SILENCE_THRESHOLD_DAYS = 3       # decay only starts after 3 days silence
COLD_PENALTY = -0.06             # applied when rupture arc starts (see E-12)
WARMTH_BONUS = 0.04              # applied on very positive exchange (delta ≥ 0.04)


def apply_delta(person_id: str, delta: float) -> float:
    """Apply attachment_delta from extraction. Returns new depth."""
    conn = get_connection()
    row = conn.execute(
        "SELECT attachment_depth FROM persons WHERE id = ?", (person_id,)
    ).fetchone()
    if row is None:
        return 0.0

    current = row["attachment_depth"]
    new_depth = _clamp(current + delta)
    conn.execute(
        "UPDATE persons SET attachment_depth = ? WHERE id = ?",
        (new_depth, person_id),
    )
    conn.commit()
    log.info("attachment_delta_applied", person_id=person_id,
             delta=delta, old=current, new=new_depth)
    return new_depth


def decay_for_silence(person_id: str, last_chat_at: datetime) -> float:
    """
    Apply silence decay if Teo has been absent for > SILENCE_THRESHOLD_DAYS.
    Called by the daily decay job (04:00). Returns new depth.
    """
    silence_days = (datetime.utcnow() - last_chat_at).days
    if silence_days <= SILENCE_THRESHOLD_DAYS:
        return _get_depth(person_id)

    # Decay proportional to silence beyond the threshold
    excess_days = silence_days - SILENCE_THRESHOLD_DAYS
    delta = -SILENCE_DECAY_PER_DAY * excess_days
    return apply_delta(person_id, delta)


def _get_depth(person_id: str) -> float:
    conn = get_connection()
    row = conn.execute(
        "SELECT attachment_depth FROM persons WHERE id = ?", (person_id,)
    ).fetchone()
    return row["attachment_depth"] if row else 0.0


def _clamp(v: float) -> float:
    return max(DEPTH_MIN, min(DEPTH_MAX, v))


def prose_label(depth: float) -> str:
    """Human-readable label for prompt context."""
    if depth > 0.7:
        return "deeply familiar"
    if depth > 0.3:
        return "comfortable and close"
    if depth > -0.3:
        return "still finding our footing"
    return "some distance between us right now"
```

## How it's read

### Affect model (`affect/model.py`)

When computing `openness` during a mood update, add a small contribution:

```python
openness = base_openness + 0.15 * attachment_depth  # clamped to [0, 1]
```

This means a deeply familiar relationship nudges her open dimension up permanently — she's more available to someone she trusts.

### Initiative engine (`initiative/engine.py`)

Outreach candidate scoring receives an attachment multiplier:

```python
def _outreach_multiplier(person_id: str) -> float:
    depth = persons.attachment.get_depth(person_id)
    # deep familiarity: she reaches out more readily
    # estrangement: she hesitates more
    return 0.6 + 0.6 * ((depth + 1.0) / 2.0)  # maps [-1,1] → [0.6, 1.2]
```

### Chat prompt

Inject in the person block:

```python
f"Your relationship with {person.name}: {prose_label(person.attachment_depth)}."
```

### Weekly self-model input (`identity/self_model.py`)

Add `attachment_depth` to `SelfModelInput.person_summary` so the Opus/Pro call can reflect on the relationship arc:

```python
person_summary = {
    "name": person.name,
    "attachment_depth": person.attachment_depth,
    "attachment_label": prose_label(person.attachment_depth),
    "last_chat_at": person.last_chat_at,
}
```

## Testing

### Unit tests — `tests/unit/test_attachment.py`

```python
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.persons.attachment import apply_delta, decay_for_silence, prose_label

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection()
    conn.execute(
        "INSERT INTO persons (id, name, attachment_depth) VALUES ('teo', 'Teo', 0.0)"
    )
    conn.commit()
    yield conn
    close()


def test_positive_delta_increases_depth(db):
    new = apply_delta("teo", 0.04)
    assert new == pytest.approx(0.04, abs=1e-6)


def test_negative_delta_decreases_depth(db):
    apply_delta("teo", 0.3)
    new = apply_delta("teo", -0.06)
    assert new == pytest.approx(0.24, abs=1e-6)


def test_clamps_at_max(db):
    apply_delta("teo", 0.99)
    new = apply_delta("teo", 0.99)
    assert new == 1.0


def test_clamps_at_min(db):
    apply_delta("teo", -0.99)
    new = apply_delta("teo", -0.99)
    assert new == -1.0


def test_no_decay_within_threshold(db):
    last_chat = datetime.utcnow() - timedelta(days=2)
    apply_delta("teo", 0.5)
    new = decay_for_silence("teo", last_chat)
    assert new == pytest.approx(0.5, abs=1e-6)


def test_decay_after_threshold(db):
    apply_delta("teo", 0.5)
    last_chat = datetime.utcnow() - timedelta(days=6)  # 3 excess days
    new = decay_for_silence("teo", last_chat)
    assert new < 0.5


def test_prose_label_deep():
    assert prose_label(0.8) == "deeply familiar"


def test_prose_label_neutral():
    assert prose_label(0.0) == "still finding our footing"
```

## Dependencies

- E-03 (affect model) — `openness` calculation extended.
- E-04 (per-chat extraction schema) — `attachment_delta` field added.
- E-10 (decay job) — `decay_for_silence` wired in at 04:00.
- H-03 (weekly self-model) — `attachment_depth` included in person summary.

## Acceptance criteria

- `persons.attachment_depth` column exists after migration.
- Three warm exchanges → depth increases; three cold or no exchanges over 6 days → depth decreases.
- `openness` in the mood snapshot is visibly higher at `attachment_depth=0.8` vs `0.0` (can verify via the debug dashboard).
- Chat prompt includes the relationship prose label.
- Weekly self-model input JSON contains `attachment_depth`.

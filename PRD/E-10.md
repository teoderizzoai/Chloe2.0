# E-10 · Memory decay daily job

## Overview

Implement `memory/store.py`: `decay_all()` applies `decay(weight, age_days, kind)` from PRD §11.7 to all memories. Schedule via the background task loop at 04:00 local time.

## Context

Without decay, episodic memories from 5 years ago are weighted as highly as yesterday's events. The decay function models the natural fading of memories — episodics fade faster than semantics (different half-lives), and autobiographical memories fade slowest of all (they're identity-constituting). The daily job runs at low-traffic hours (04:00) to avoid competing with the weekly self-model (03:00) and consolidation jobs.

## Decay function (PRD §11.7)

```
new_weight = weight * 0.5 ^ (age_days / half_life_days)
```

Half-lives by kind:
| Kind | Half-life (days) |
|------|-----------------|
| episodic | 60 |
| semantic | 365 |
| autobiographical | 1825 (5 years) |
| procedural | 180 |

Minimum weight floor: `0.01` (never fully zero — memories remain retrievable).

## Implementation

```python
# chloe/memory/store.py

from datetime import datetime, timedelta
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger
import math

log = get_logger("memory.store")

HALF_LIVES = {
    "episodic":       60,
    "semantic":       365,
    "autobiographical": 1825,
    "procedural":     180,
}
WEIGHT_FLOOR = 0.01


def decay(weight: float, age_days: float, kind: str) -> float:
    """Apply exponential decay to a memory weight."""
    half_life = HALF_LIVES.get(kind, 180)
    if age_days <= 0:
        return weight
    new_weight = weight * (0.5 ** (age_days / half_life))
    return max(new_weight, WEIGHT_FLOOR)


def decay_all(now: datetime | None = None, dry_run: bool = False) -> dict:
    """
    Apply decay to all non-archived memories.
    Updates weight in the DB. Returns report dict.
    """
    if now is None:
        now = datetime.utcnow()

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, kind, weight, created_at
        FROM memories
        WHERE (archived_tier IS NULL OR archived_tier = 'hot')
          AND weight > ?
        """,
        (WEIGHT_FLOOR,),
    ).fetchall()

    updated = 0
    unchanged = 0

    for row in rows:
        try:
            created = datetime.fromisoformat(row["created_at"])
        except ValueError:
            continue

        age_days = (now - created).total_seconds() / 86400
        new_weight = decay(row["weight"], age_days, row["kind"])

        if abs(new_weight - row["weight"]) < 0.0001:
            unchanged += 1
            continue

        if not dry_run:
            conn.execute(
                "UPDATE memories SET weight=? WHERE id=?",
                (new_weight, row["id"])
            )
        updated += 1

    if not dry_run:
        conn.commit()

    log.info("decay_all_complete",
             total=len(rows), updated=updated, unchanged=unchanged,
             dry_run=dry_run)
    return {"total": len(rows), "updated": updated, "unchanged": unchanged}


async def run_daily_decay():
    """Background task entrypoint: runs decay at 04:00 local time."""
    import asyncio
    from datetime import date, time as dtime, datetime
    import pytz

    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), dtime(4, 0))
        if now >= target:
            target = datetime.combine(now.date() + timedelta(days=1), dtime(4, 0))
        wait_seconds = (target - now).total_seconds()
        log.debug("decay_job_sleeping", seconds=round(wait_seconds))
        await asyncio.sleep(wait_seconds)

        try:
            report = decay_all()
            log.info("decay_job_ran", **report)
        except Exception as exc:
            log.warning("decay_job_error", error=str(exc))
```

## Wire into `loop.py`

```python
# In loop.py: add to background tasks
from chloe.memory.store import run_daily_decay

async def main():
    # ... other startup ...
    asyncio.create_task(run_daily_decay())
```

## Dependencies

- F-04 (`memories` table with `weight`, `kind`, `created_at`, `archived_tier` columns).
- F-11 (`loop.py` — background task scheduling).

## Testing

### Unit tests — `tests/unit/test_memory_decay.py`

```python
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.memory.store import decay, decay_all, HALF_LIVES, WEIGHT_FLOOR

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_decay_half_life_episodic():
    """At exactly one half-life, weight should halve."""
    weight = 1.0
    result = decay(weight, age_days=60, kind="episodic")
    assert abs(result - 0.5) < 0.01


def test_decay_60_day_episodic_halves_weight(db):
    """A 60-day-old episodic memory should have half its original weight after decay_all."""
    from chloe.actions.schema import ulid
    memory_id = ulid()
    created = datetime.utcnow() - timedelta(days=60)
    initial_weight = 1.0

    db.execute(
        "INSERT INTO memories (id, kind, text, source, weight, artifact_refs, tags, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (memory_id, "episodic", "test memory", "test", initial_weight, "[]", "[]", created.isoformat()),
    )
    db.commit()

    report = decay_all()
    assert report["updated"] >= 1

    row = db.execute("SELECT weight FROM memories WHERE id=?", (memory_id,)).fetchone()
    assert abs(row["weight"] - 0.5) < 0.05


def test_decay_weight_floor():
    """Weight should never go below WEIGHT_FLOOR."""
    result = decay(0.001, age_days=1000, kind="episodic")
    assert result >= WEIGHT_FLOOR


def test_decay_semantic_slower_than_episodic():
    """Semantic memories decay slower than episodic."""
    ep = decay(1.0, age_days=100, kind="episodic")
    sem = decay(1.0, age_days=100, kind="semantic")
    assert sem > ep


def test_decay_autobiographical_slowest():
    """Autobiographical memories decay slowest."""
    ep = decay(1.0, age_days=200, kind="episodic")
    sem = decay(1.0, age_days=200, kind="semantic")
    auto = decay(1.0, age_days=200, kind="autobiographical")
    assert auto > sem > ep


def test_decay_dry_run_does_not_write(db):
    from chloe.actions.schema import ulid
    memory_id = ulid()
    created = datetime.utcnow() - timedelta(days=120)
    db.execute(
        "INSERT INTO memories (id, kind, text, source, weight, artifact_refs, tags, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (memory_id, "episodic", "test", "test", 1.0, "[]", "[]", created.isoformat()),
    )
    db.commit()

    decay_all(dry_run=True)

    # Weight should be unchanged
    row = db.execute("SELECT weight FROM memories WHERE id=?", (memory_id,)).fetchone()
    assert abs(row["weight"] - 1.0) < 0.001


def test_decay_all_respects_archived_tier(db):
    """Archived (cold tier) memories should not be decayed."""
    from chloe.actions.schema import ulid
    memory_id = ulid()
    created = datetime.utcnow() - timedelta(days=100)
    db.execute(
        "INSERT INTO memories (id, kind, text, source, weight, artifact_refs, tags, created_at, archived_tier) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (memory_id, "episodic", "test", "test", 1.0, "[]", "[]", created.isoformat(), "cold"),
    )
    db.commit()

    decay_all()

    row = db.execute("SELECT weight FROM memories WHERE id=?", (memory_id,)).fetchone()
    assert abs(row["weight"] - 1.0) < 0.001  # Unchanged
```

## Acceptance criteria

- 60-day-old episodic memory → `decay_all()` sets `weight ≈ 0.5` (half of original 1.0).
- `weight` never goes below `WEIGHT_FLOOR = 0.01`.
- Semantic memories decay slower than episodic over the same period.
- Autobiographical memories decay slowest.
- `dry_run=True` → no DB writes.
- `archived_tier="cold"` memories skipped by `decay_all()`.

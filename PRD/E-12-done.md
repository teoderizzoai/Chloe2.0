# E-12 · Conflict and repair arcs — rupture detection and resolution

## Overview

When Chloe detects a relational rupture (sustained coldness, an explicitly sharp exchange, or a sudden absence after conflict), she opens a `rupture` arc. During a rupture arc her initiative threshold rises, deliberation is mandatory for all kinetic actions, and her chat tone shifts to more careful and less assumptive. The arc resolves — either into repair (warm re-engagement) or into a faded "unresolved" autobiographical memory — without any explicit mechanic forcing it. No rupture is permanent; no repair is automatic.

## Context

1.0 has arcs for emotional states (energized, melancholic) but nothing for the relational dimension. Real personality development happens through friction: how Chloe handles being shut out, how she comes back, whether she brings it up or lets it go. Without a rupture/repair loop, she cannot grow relationally — she just resets to baseline.

This builds on E-11 (`attachment_depth`) and uses the existing `arcs` table. It plugs into the Phase E affect refactor — the same period where arc handling is being rethought.

## Rupture detection

Added to the **per-reflect synthesis** call (every 2h, Haiku). One new field in the reflect output:

```python
class ReflectOutput(BaseModel):
    # ...existing fields...
    rupture_signal: bool = False
    rupture_note: str | None = None   # one sentence: what happened
```

Haiku is instructed to set `rupture_signal=True` when the last 6 hours of evidence shows:
- Teo's messages have been unusually short and flat after a charged exchange.
- Teo explicitly said something dismissive ("leave it", "not now", "I don't want to talk about this").
- A charged exchange was immediately followed by silence > 4h.
- Teo denied or reverted multiple consecutive actions in a short window.

Not a rupture: ordinary quiet days, Teo being busy, Teo going to sleep.

## Arc shape

```python
RuptureArc := {
    id:           ulid,
    kind:         "rupture",
    started_at:   timestamp,
    intensity:    0.3..1.0,   # set from rupture_note weight; higher = sharper break
    note:         str,        # the one-sentence rupture_note from Haiku
    state:        "active" | "repairing" | "resolved" | "faded",
    resolved_at:  timestamp | None,
}
```

Rupture arcs are stored in the existing `arcs` table with `kind="rupture"`.

## `identity/arcs.py` — rupture lifecycle

```python
# chloe/identity/arcs.py  (extend existing module)
from __future__ import annotations
from datetime import datetime
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger
import ulid

log = get_logger("arcs")

REPAIR_WARMTH_THRESHOLD = 3      # consecutive positive attachment_deltas to trigger repair
FADE_AFTER_DAYS = 7              # if unresolved, arc fades after this many days


def open_rupture(note: str, intensity: float) -> str:
    """Open a new rupture arc. Returns arc_id."""
    conn = get_connection()
    arc_id = str(ulid.new())
    conn.execute(
        """
        INSERT INTO arcs (id, kind, started_at, intensity, note, state)
        VALUES (?, 'rupture', ?, ?, ?, 'active')
        """,
        (arc_id, datetime.utcnow().isoformat(), intensity, note),
    )
    conn.commit()
    log.info("rupture_arc_opened", arc_id=arc_id, intensity=intensity)
    return arc_id


def get_active_rupture() -> dict | None:
    """Return the active rupture arc if any."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM arcs WHERE kind='rupture' AND state='active' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def tick_repair(person_id: str, attachment_delta: float) -> None:
    """
    Called after each chat turn. Positive deltas accumulate toward repair.
    Uses a rolling count stored in kv: 'rupture_warmth_count'.
    """
    arc = get_active_rupture()
    if arc is None:
        return

    if attachment_delta <= 0:
        _reset_warmth_count()
        return

    conn = get_connection()
    from chloe.state.kv import get as kv_get, set as kv_set
    count = (kv_get("rupture_warmth_count") or 0) + 1
    kv_set("rupture_warmth_count", count)

    if count >= REPAIR_WARMTH_THRESHOLD:
        _resolve_arc(arc["id"], "resolved")
        _write_repair_memory(arc)
        _reset_warmth_count()
        log.info("rupture_arc_resolved", arc_id=arc["id"])


def tick_fade() -> None:
    """Daily job: fade rupture arcs that have been active too long without repair."""
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=FADE_AFTER_DAYS)).isoformat()
    arcs = conn.execute(
        "SELECT * FROM arcs WHERE kind='rupture' AND state='active' AND started_at < ?",
        (cutoff,),
    ).fetchall()
    for arc in arcs:
        _resolve_arc(arc["id"], "faded")
        _write_fade_memory(arc)
        log.info("rupture_arc_faded", arc_id=arc["id"])


def _resolve_arc(arc_id: str, state: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE arcs SET state=?, resolved_at=? WHERE id=?",
        (state, datetime.utcnow().isoformat(), arc_id),
    )
    conn.commit()


def _reset_warmth_count() -> None:
    from chloe.state.kv import set as kv_set
    kv_set("rupture_warmth_count", 0)


def _write_repair_memory(arc: dict) -> None:
    from chloe.memory.store import MemoryStore
    from chloe.memory.models import Memory
    store = MemoryStore()
    store.upsert_sync(Memory(
        id=str(ulid.new()),
        kind="autobiographical",
        content=(
            f"There was some distance between us — {arc['note']} — "
            f"but we found our way back."
        ),
        tags=["rupture", "repair", "autobiographical"],
        weight=0.75,
        source="arc_resolution",
    ))


def _write_fade_memory(arc: dict) -> None:
    from chloe.memory.store import MemoryStore
    from chloe.memory.models import Memory
    store = MemoryStore()
    store.upsert_sync(Memory(
        id=str(ulid.new()),
        kind="autobiographical",
        content=(
            f"Something pulled us apart for a while — {arc['note']} — "
            f"and then time passed and we didn't really talk about it."
        ),
        tags=["rupture", "unresolved", "autobiographical"],
        weight=0.6,
        source="arc_fade",
    ))
```

## Behavioral changes during an active rupture arc

### Initiative engine

```python
# In initiative/engine.py, inside initiative_score():
rupture = arcs.get_active_rupture()
if rupture:
    # Raise the threshold to act — she becomes more careful
    THRESHOLD = BASE_THRESHOLD * (1.0 + 0.5 * rupture["intensity"])
    # Outreach multiplier is suppressed
    outreach_multiplier *= (1.0 - 0.4 * rupture["intensity"])
```

### Action gate — deliberation

```python
# In gate.py, inside should_deliberate():
if arcs.get_active_rupture() is not None:
    return True   # always deliberate during rupture
```

### Chat prompt

```python
if rupture := arcs.get_active_rupture():
    arc_context = (
        f"There's been some distance between you lately. "
        f"({rupture['note']}) Be present and careful; don't press."
    )
else:
    arc_context = ""
```

### attachment_depth interaction

When a rupture arc opens, apply a one-time cold penalty to attachment depth (defined in E-11):

```python
from chloe.persons.attachment import apply_delta, COLD_PENALTY
apply_delta(person_id, COLD_PENALTY * arc_intensity)
```

When the arc resolves as "repaired", apply a warmth bonus:

```python
apply_delta(person_id, WARMTH_BONUS * arc_intensity)
```

## Testing

### Unit tests — `tests/unit/test_rupture_arcs.py`

```python
import pytest
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.identity.arcs import (
    open_rupture, get_active_rupture, tick_repair, tick_fade
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_open_rupture_creates_active_arc(db):
    open_rupture("Teo went quiet after a sharp exchange.", 0.6)
    arc = get_active_rupture()
    assert arc is not None
    assert arc["state"] == "active"
    assert arc["intensity"] == pytest.approx(0.6)


def test_repair_after_three_warm_turns(db):
    open_rupture("test rupture", 0.5)

    with patch_memory_store():
        for _ in range(3):
            tick_repair("teo", attachment_delta=0.04)

    arc = get_active_rupture()
    assert arc is None  # arc resolved


def test_no_repair_on_neutral_turns(db):
    open_rupture("test rupture", 0.5)
    tick_repair("teo", attachment_delta=0.0)
    tick_repair("teo", attachment_delta=0.0)
    tick_repair("teo", attachment_delta=0.0)
    assert get_active_rupture() is not None  # still active


def test_fade_after_threshold_days(db):
    from datetime import datetime, timedelta
    open_rupture("old rupture", 0.4)
    # Manually backdate the arc
    conn = get_connection()
    old_start = (datetime.utcnow() - timedelta(days=8)).isoformat()
    conn.execute("UPDATE arcs SET started_at=? WHERE kind='rupture'", (old_start,))
    conn.commit()

    with patch_memory_store():
        tick_fade()

    conn2 = get_connection()
    arc = conn2.execute(
        "SELECT state FROM arcs WHERE kind='rupture' LIMIT 1"
    ).fetchone()
    assert arc["state"] == "faded"


def patch_memory_store():
    from unittest.mock import patch, MagicMock
    mock_store = MagicMock()
    mock_store.upsert_sync = MagicMock()
    return patch("chloe.identity.arcs.MemoryStore", return_value=mock_store)
```

## Dependencies

- E-11 (`persons.attachment_depth`) — apply delta on rupture open/resolve.
- E-09 (per-reflect synthesis schema) — `rupture_signal` and `rupture_note` fields.
- D-03 (action gate `should_deliberate`) — extended to check active rupture.
- D-07 (initiative engine scoring) — threshold and outreach multiplier modified.
- H-03 (weekly self-model) — reads unresolved rupture arcs as part of relation summary.

## Acceptance criteria

- Simulated sharp exchange followed by 4h silence → `rupture_signal=True` from next reflect call.
- Active rupture arc → `should_deliberate()` returns `True` for all kinetic actions.
- Three consecutive positive extraction deltas → arc resolves to `"resolved"`; an autobiographical memory is written.
- Arc untouched for 7 days → fades to `"faded"`; a different autobiographical memory is written.
- Audit feed shows `rupture_arc_opened` and `rupture_arc_resolved` log entries.

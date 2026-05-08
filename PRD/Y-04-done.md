# Y-04 · Emotional Continuity Across Sessions

## Overview

Chloe's affect state currently lives in-memory and resets to neutral on restart. This adds an `affect_checkpoint` written to KV at session end (and periodically during long runs), restored on startup, and decayed toward neutral over elapsed time. The result: emotions persist across conversations, decay naturally when time passes, and accumulate when events reinforce them. An absence creates mild concern; completing a goal creates satisfaction that lasts hours.

## Context

As of H-series, the affect system (`affect/`) tracks valence, arousal, and emotional residue within a session. But every restart is emotionally blank. Teo doesn't notice, but Chloe is doing something subtly broken: she has no emotional memory between sessions, which breaks the continuity the AGI-feeling requires.

The fix is small: serialise the current affect state to KV at the right moments, restore it at startup, apply a time-based decay so emotions don't freeze indefinitely.

Decay model:
- Valence decays toward 0 with a half-life of ~6 hours.
- Arousal decays toward 0.2 (slight baseline activation) with a half-life of ~2 hours.
- Label is re-derived from post-decay (valence, arousal) using the existing label function.

Additional emotion events (absence penalty, goal completion boost):
- If >48 hours since last chat message from Teo → inject a mild negative valence drift (-0.1).
- If a goal is marked `done` → inject a positive valence pulse (+0.15, arousal +0.1) lasting ~4 hours.

**When:** Phase Y. Depends on `affect/` module, `state/kv.py`, memory/store for goal events.

---

## Checkpoint schema (KV key: `affect_checkpoint`)

```json
{
  "valence": 0.35,
  "arousal": 0.42,
  "label": "content",
  "saved_at": "2026-05-06T09:15:00"
}
```

---

## New module: `affect/continuity.py`

```python
# chloe/affect/continuity.py
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("affect.continuity")

CHECKPOINT_KEY = "affect_checkpoint"

VALENCE_HALF_LIFE_H   = 6.0    # hours
AROUSAL_HALF_LIFE_H   = 2.0
AROUSAL_BASELINE      = 0.2    # arousal never decays below this

ABSENCE_THRESHOLD_H   = 48     # hours without Teo contact → mild negative drift
ABSENCE_VALENCE_DRIFT = -0.10

GOAL_DONE_VALENCE     = +0.15
GOAL_DONE_AROUSAL     = +0.10
GOAL_DONE_DURATION_H  = 4.0    # how long the boost persists at full strength


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_checkpoint(valence: float, arousal: float, label: str) -> None:
    state = {
        "valence": round(valence, 4),
        "arousal": round(arousal, 4),
        "label": label,
        "saved_at": datetime.utcnow().isoformat(),
    }
    kv_set(CHECKPOINT_KEY, json.dumps(state))
    log.debug("affect_checkpoint_saved", valence=valence, arousal=arousal, label=label)


# ---------------------------------------------------------------------------
# Restore + decay
# ---------------------------------------------------------------------------

def restore_checkpoint() -> dict:
    """
    Load the saved affect state and apply time-based decay.
    Returns {"valence": float, "arousal": float, "label": str}.
    """
    raw = kv_get(CHECKPOINT_KEY)
    if not raw:
        return _neutral()

    try:
        state = json.loads(raw)
    except Exception:
        return _neutral()

    saved_at = _parse_dt(state.get("saved_at", ""))
    if saved_at is None:
        return _neutral()

    elapsed_h = (datetime.utcnow() - saved_at).total_seconds() / 3600.0

    decayed_valence = _decay(state.get("valence", 0.0), 0.0, elapsed_h, VALENCE_HALF_LIFE_H)
    decayed_arousal = _decay(state.get("arousal", 0.2), AROUSAL_BASELINE, elapsed_h, AROUSAL_HALF_LIFE_H)

    # Apply absence penalty if needed
    decayed_valence = _apply_absence_penalty(decayed_valence, elapsed_h)

    label = _rederive_label(decayed_valence, decayed_arousal)

    log.info(
        "affect_checkpoint_restored",
        elapsed_h=round(elapsed_h, 1),
        valence=round(decayed_valence, 3),
        arousal=round(decayed_arousal, 3),
        label=label,
    )
    return {"valence": decayed_valence, "arousal": decayed_arousal, "label": label}


# ---------------------------------------------------------------------------
# Goal completion pulse
# ---------------------------------------------------------------------------

def apply_goal_completion_pulse(current_valence: float, current_arousal: float) -> tuple[float, float]:
    """
    Called when a goal transitions to 'done'.
    Returns updated (valence, arousal).
    """
    new_valence = min(1.0, current_valence + GOAL_DONE_VALENCE)
    new_arousal = min(1.0, current_arousal + GOAL_DONE_AROUSAL)
    log.info("goal_completion_pulse", delta_v=GOAL_DONE_VALENCE, delta_a=GOAL_DONE_AROUSAL)
    return new_valence, new_arousal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decay(current: float, target: float, elapsed_h: float, half_life_h: float) -> float:
    """Exponential decay from current toward target."""
    if elapsed_h <= 0:
        return current
    k = math.log(2) / half_life_h
    decayed = target + (current - target) * math.exp(-k * elapsed_h)
    return round(max(-1.0, min(1.0, decayed)), 4)


def _apply_absence_penalty(valence: float, elapsed_h: float) -> float:
    """Apply a mild negative drift if no contact in >48h."""
    if elapsed_h < ABSENCE_THRESHOLD_H:
        return valence
    excess_h = elapsed_h - ABSENCE_THRESHOLD_H
    penalty = ABSENCE_VALENCE_DRIFT * min(1.0, excess_h / 24.0)
    result = max(-0.5, valence + penalty)
    log.debug("absence_penalty_applied", elapsed_h=elapsed_h, penalty=round(penalty, 3))
    return result


def _rederive_label(valence: float, arousal: float) -> str:
    try:
        from chloe.affect.label import label_from_dims
        return label_from_dims(valence, arousal)
    except Exception:
        if valence > 0.3:
            return "content"
        elif valence < -0.3:
            return "low"
        return "neutral"


def _neutral() -> dict:
    return {"valence": 0.0, "arousal": AROUSAL_BASELINE, "label": "neutral"}


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
```

---

## Integration

### Save checkpoint at session end and periodically

```python
# In loop.py — add to shutdown handler:
from chloe.affect.continuity import save_checkpoint
from chloe.affect.dims import get_current_dims    # existing affect state accessor

async def shutdown():
    dims = get_current_dims()
    save_checkpoint(dims.valence, dims.arousal, dims.label)
    log.info("affect_checkpoint_saved_on_shutdown")
```

```python
# In loop.py — after each tick, save if valence/arousal changed significantly:
_last_saved_valence = 0.0

async def _maybe_save_checkpoint(dims) -> None:
    global _last_saved_valence
    if abs(dims.valence - _last_saved_valence) > 0.05:
        save_checkpoint(dims.valence, dims.arousal, dims.label)
        _last_saved_valence = dims.valence
```

### Restore checkpoint at startup

```python
# In app.py:
from chloe.affect.continuity import restore_checkpoint
from chloe.affect.dims import set_dims    # existing setter

@app.on_event("startup")
async def on_startup():
    restored = restore_checkpoint()
    set_dims(restored["valence"], restored["arousal"])
    log.info("affect_restored", **restored)
```

### Goal completion pulse

```python
# In wherever goals are marked done (initiative/candidates.py or actions/deliberate.py):
from chloe.affect.continuity import apply_goal_completion_pulse
from chloe.affect.dims import get_current_dims, set_dims

dims = get_current_dims()
new_v, new_a = apply_goal_completion_pulse(dims.valence, dims.arousal)
set_dims(new_v, new_a)
```

---

## Testing

### `tests/unit/test_affect_continuity.py`

```python
import pytest
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.affect.continuity import (
    save_checkpoint,
    restore_checkpoint,
    apply_goal_completion_pulse,
    _decay,
    _apply_absence_penalty,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_save_and_restore_no_decay(db):
    save_checkpoint(0.5, 0.6, "happy")
    result = restore_checkpoint()
    # Immediately restored — minimal decay (< 1 second)
    assert abs(result["valence"] - 0.5) < 0.05
    assert abs(result["arousal"] - 0.6) < 0.05


def test_restore_with_elapsed_time_decays(db):
    past = (datetime.utcnow() - timedelta(hours=6)).isoformat()
    kv_set("affect_checkpoint", json.dumps({
        "valence": 0.8, "arousal": 0.8, "label": "elated", "saved_at": past
    }))
    result = restore_checkpoint()
    # After 6h, valence should be ~0.4 (one half-life)
    assert result["valence"] < 0.6
    assert result["arousal"] < 0.6


def test_restore_missing_checkpoint_returns_neutral(db):
    result = restore_checkpoint()
    assert result["valence"] == 0.0
    assert result["label"] == "neutral"


def test_decay_half_life_math():
    result = _decay(current=1.0, target=0.0, elapsed_h=6.0, half_life_h=6.0)
    assert abs(result - 0.5) < 0.01    # exactly one half-life


def test_decay_zero_elapsed_unchanged():
    result = _decay(0.7, 0.0, 0.0, 6.0)
    assert result == pytest.approx(0.7)


def test_absence_penalty_under_threshold_no_effect():
    result = _apply_absence_penalty(0.5, elapsed_h=24.0)
    assert result == pytest.approx(0.5)


def test_absence_penalty_over_threshold_applied():
    result = _apply_absence_penalty(0.5, elapsed_h=72.0)
    assert result < 0.5


def test_goal_completion_pulse_increases_valence():
    new_v, new_a = apply_goal_completion_pulse(0.3, 0.3)
    assert new_v > 0.3
    assert new_a > 0.3


def test_goal_completion_pulse_capped_at_one():
    new_v, new_a = apply_goal_completion_pulse(0.95, 0.95)
    assert new_v <= 1.0
    assert new_a <= 1.0


def test_checkpoint_key_written_to_kv(db):
    save_checkpoint(0.4, 0.5, "calm")
    raw = kv_get("affect_checkpoint")
    assert raw is not None
    data = json.loads(raw)
    assert data["valence"] == pytest.approx(0.4)
    assert data["label"] == "calm"
```

---

## Dependencies

- `affect/dims.py` — `get_current_dims()`, `set_dims()` (existing setters/getters).
- `affect/label.py` — `label_from_dims()` (existing labeller).
- `state/kv.py` — KV get/set.
- `app.py` startup hook and `loop.py` shutdown hook.

## Acceptance criteria

- On startup, affect state restored from `affect_checkpoint` with correct decay applied.
- Missing checkpoint → neutral state, no exception.
- After 6h elapsed, a valence of 0.8 decays to ~0.4 (one half-life).
- After 48h+ without contact, restored valence is reduced by at most 0.10.
- Goal completion → valence +0.15, arousal +0.10, capped at 1.0.
- Checkpoint saved on shutdown and whenever valence changes by >0.05.
- The `affect_checkpoint` KV key is always valid JSON after any save.

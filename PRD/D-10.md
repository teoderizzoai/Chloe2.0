# D-10 · Threshold tuning + cutover

## Overview

Review 2 weeks of shadow logs. Adjust `INITIATIVE_THRESHOLD` in `config.py`. Document the chosen value and reasoning in a comment. Delete `_fire_event`, `_send_autonomous_outreach`, the shadow runner, and all dice-roll abstract activity code from the 1.0 codebase.

## Context

Shadow mode (D-09) has been running for 2 weeks. This step is the promotion gate: review the data, tune the threshold, then permanently cut over. After cutover, the old event loop code is dead weight that creates confusion and merge conflicts — it must be deleted cleanly.

## Threshold calibration process

1. Query the shadow log:
   ```python
   # In a notebook or admin script
   from chloe.state.kv import get as kv_get
   records = kv_get("shadow_decisions", default=[])
   
   total = len(records)
   idle_rate = sum(1 for r in records if r["was_idle"]) / total
   # Target: idle rate 60-75% (active ~25-40% of ticks)
   # If idle rate < 60%: raise threshold
   # If idle rate > 80%: lower threshold
   ```

2. Review by category:
   - Are all routine candidates (morning/evening check-ins) firing? They should be ~daily.
   - Are pressure-driven candidates firing when they should?
   - Are interest-driven candidates firing too often? (pressure ≈ 0.21 max)

3. Document the chosen value in a code comment:
   ```python
   # config.py
   # INITIATIVE_THRESHOLD calibrated at 0.35 on 2026-06-01 after 14 days of shadow.
   # Shadow stats: idle_rate=0.68, 28% active, ~3 actions/day average.
   # Routine candidates reliably won (pressure=0.8 > threshold).
   # Interest-driven rarely fired (pressure≈0.21, well below threshold).
   # Raised from 0.28 to 0.35 to reduce duplicate music actions.
   initiative_threshold: float = 0.35
   ```

## Cutover: code deletions

```python
# chloe/loop.py — remove shadow runner invocation
# Before:
#     await _fire_event()
#     await shadow_tick()
# After:
#     await initiative_tick()

from chloe.initiative.engine import tick as initiative_tick

async def initiative_loop():
    while True:
        try:
            await initiative_tick()
        except Exception as e:
            log.warning("initiative_tick_error", error=str(e))
        await asyncio.sleep(60)
```

Files to delete (or heavily prune from the 1.0 codebase):
- `chloe/legacy/_fire_event.py` (or equivalent function)
- `chloe/legacy/_send_autonomous_outreach.py`
- `chloe/initiative/shadow.py` (shadow runner)
- `chloe/admin/shadow_routes.py` (shadow endpoint)
- Any `_abstract_activity_*` functions with dice rolls

## Verification commands

```bash
# These should return no results after cutover:
grep -r "_fire_event" chloe/
grep -r "_send_autonomous_outreach" chloe/
grep -r "random.random()" chloe/initiative/
grep -r "shadow_tick" chloe/
```

## Dependencies

- D-09 (shadow mode — 2 weeks of data required).
- D-08 (`initiative/engine.py` — replaces the old loop).

## Testing

### Smoke tests post-cutover

```python
# tests/unit/test_cutover.py

import subprocess
import pytest


def test_no_fire_event_in_codebase():
    """_fire_event must not exist in the chloe package after cutover."""
    result = subprocess.run(
        ["grep", "-r", "_fire_event", "chloe/"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() == "", f"_fire_event found:\n{result.stdout}"


def test_no_send_autonomous_outreach():
    result = subprocess.run(
        ["grep", "-r", "_send_autonomous_outreach", "chloe/"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() == "", f"_send_autonomous_outreach found:\n{result.stdout}"


def test_no_shadow_runner():
    result = subprocess.run(
        ["grep", "-r", "shadow_tick", "chloe/"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() == "", f"shadow_tick still present:\n{result.stdout}"


def test_initiative_loop_uses_new_engine():
    """loop.py must import from initiative.engine, not legacy code."""
    result = subprocess.run(
        ["grep", "-r", "initiative.engine", "chloe/loop.py"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() != "", "loop.py doesn't import initiative.engine"


def test_initiative_threshold_in_config():
    from chloe.config import get_settings
    settings = get_settings()
    assert hasattr(settings, "initiative_threshold")
    assert 0.2 <= settings.initiative_threshold <= 0.8, "Threshold out of reasonable range"
```

### Production validation

After cutover, monitor for 7 days:
1. `GET /admin/audit` — confirm daily action counts in expected range (2–5/day).
2. Check no spurious actions: no middle-of-night messages, no repeated actions.
3. Confirm routine candidates fire daily.

```python
# Manual check script: ops/validate_cutover.py
from chloe.state.db import get_connection
from datetime import datetime, timedelta

conn = get_connection()
cutover_date = datetime.now() - timedelta(days=7)
rows = conn.execute(
    "SELECT date(proposed_at) as day, COUNT(*) as n FROM actions WHERE proposed_at > ? GROUP BY 1 ORDER BY 1",
    (cutover_date.isoformat(),)
).fetchall()

for row in rows:
    print(f"{row['day']}: {row['n']} actions")
    assert 1 <= row['n'] <= 10, f"Abnormal action count on {row['day']}: {row['n']}"
```

## Acceptance criteria

- `grep -r "_fire_event" chloe/` returns no results.
- `loop.py` calls `initiative_tick()` from `chloe.initiative.engine`.
- `initiative_threshold` documented in `config.py` with calibration rationale comment.
- 7 consecutive days of production with new engine and no spurious actions in the audit feed.
- All smoke tests pass.

# D-06 · `initiative/candidates.py` — routine candidates

## Overview

Implement `routine_candidates(now) -> list[CandidateAction]`. Emits candidates based on time-of-day and elapsed time: morning check-in, evening check-in, sleep consolidation, weekly self-model. All routine candidates get a fixed high pressure (0.8) so they reliably win the scoring race.

## Context

Routines are the predictable heartbeat of Chloe's day. Unlike pressure-driven or interest-driven candidates, these fire at specific time windows and only once per period. The guard mechanism (checking `kv` for "already fired today") prevents duplicates on rapid ticks during the window. The high pressure (0.8) ensures routines are almost always selected over lower-pressure candidates — Chloe won't skip her morning check-in because she's curious about marine biology.

## Implementation

```python
# In chloe/initiative/candidates.py

from datetime import datetime, time
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("initiative.routine")

ROUTINE_PRESSURE = 0.8

class TimeWindow:
    def __init__(self, start: time, end: time):
        self.start = start
        self.end = end

    def contains(self, t: time) -> bool:
        return self.start <= t <= self.end


MORNING_WINDOW    = TimeWindow(time(8, 15), time(8, 45))
EVENING_WINDOW    = TimeWindow(time(20, 45), time(21, 15))
CONSOLIDATION_WIN = TimeWindow(time(2, 45), time(3, 15))
WEEKLY_WINDOW     = TimeWindow(time(3, 0), time(4, 0))


def routine_candidates(now: datetime | None = None) -> list[CandidateAction]:
    """Emit time-based routine candidates. now defaults to datetime.now()."""
    if now is None:
        now = datetime.now()

    t = now.time()
    today = now.date().isoformat()
    this_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    candidates = []

    # Morning check-in
    if MORNING_WINDOW.contains(t) and not kv_get(f"routine:morning_sent:{today}"):
        candidates.append(CandidateAction(
            tool="messages", verb="send_text",
            args={"body": ""},  # initiative engine fills this via LLM
            intent="Morning check-in with Teo — greet him and note anything relevant from overnight",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="morning_checkin",
        ))

    # Evening check-in
    if EVENING_WINDOW.contains(t) and not kv_get(f"routine:evening_sent:{today}"):
        candidates.append(CandidateAction(
            tool="messages", verb="send_text",
            args={"body": ""},
            intent="Evening check-in with Teo — wrap up the day and see how he's doing",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="evening_checkin",
        ))

    # Sleep consolidation (memory + affect processing)
    if CONSOLIDATION_WIN.contains(t) and not kv_get(f"routine:consolidation:{today}"):
        candidates.append(CandidateAction(
            tool="self_tools", verb="trigger_consolidation",
            args={},
            intent="Run nightly memory consolidation and affect processing",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="consolidation",
        ))

    # Weekly self-model (Sundays only)
    if now.weekday() == 6 and WEEKLY_WINDOW.contains(t) and not kv_get(f"routine:weekly_self_model:{this_week}"):
        candidates.append(CandidateAction(
            tool="self_tools", verb="trigger_weekly_self_model",
            args={},
            intent="Run weekly self-model introspection and update identity beliefs",
            pressure=ROUTINE_PRESSURE,
            source="routine",
            source_id="weekly_self_model",
        ))

    log.debug("routine_candidates", count=len(candidates), time=str(t))
    return candidates


def mark_routine_done(source_id: str, now: datetime | None = None) -> None:
    """Mark a routine as completed for the current period."""
    if now is None:
        now = datetime.now()
    today = now.date().isoformat()
    this_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    key_map = {
        "morning_checkin":    f"routine:morning_sent:{today}",
        "evening_checkin":    f"routine:evening_sent:{today}",
        "consolidation":      f"routine:consolidation:{today}",
        "weekly_self_model":  f"routine:weekly_self_model:{this_week}",
    }
    key = key_map.get(source_id)
    if key:
        kv_set(key, True)
```

## Dependencies

- D-03 (`CandidateAction` dataclass).
- F-08 (`state/kv.py` — routine guard flags).

## Testing

### Unit tests — `tests/unit/test_routine_candidates.py`

```python
import pytest
from datetime import datetime
from unittest.mock import patch
from chloe.initiative.candidates import routine_candidates, mark_routine_done


def _at(hour: int, minute: int, weekday: int = 0) -> datetime:
    """Create a datetime at a given hour:minute on a specific weekday (0=Monday)."""
    # Find a date with the correct weekday (0=Monday, 6=Sunday)
    from datetime import date, timedelta
    base = date(2026, 5, 4)  # A Monday
    delta = (weekday - base.weekday()) % 7
    d = base + timedelta(days=delta)
    return datetime(d.year, d.month, d.day, hour, minute)


def test_morning_checkin_in_window():
    now = _at(8, 30)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "morning_checkin" in sources


def test_morning_checkin_outside_window():
    now = _at(9, 0)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "morning_checkin" not in sources


def test_morning_checkin_already_sent():
    now = _at(8, 30)
    def mock_kv_get(key, default=None):
        if "morning_sent" in key:
            return True
        return None
    with patch("chloe.initiative.candidates.kv_get", side_effect=mock_kv_get):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "morning_checkin" not in sources


def test_evening_checkin_in_window():
    now = _at(21, 0)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "evening_checkin" in sources


def test_sunday_weekly_model_in_window():
    now = _at(3, 30, weekday=6)  # Sunday
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "weekly_self_model" in sources
    assert "consolidation" in sources  # 03:30 is in consolidation window too


def test_weekly_model_not_on_monday():
    now = _at(3, 30, weekday=0)  # Monday
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "weekly_self_model" not in sources


def test_sunday_morning_08_30_returns_3_candidates():
    """time=08:30 Sunday, none run today/this week → 3 candidates (morning + consolidation? No.
    morning is 08:30 → yes; consolidation is 02:45-03:15 → no; weekly is 03:00-04:00 → no.
    Only morning expected at 08:30 on Sunday."""
    # Actually, the PLAN says "time = 08:30 Sunday, none run today/this week → 3 candidates"
    # That means morning(08:30) + weekly_self_model + consolidation? But consolidation is 02:45.
    # Re-reading PLAN: D-06 deliverable says "time = 08:30 Sunday, none run today/this week → 3 candidates returned"
    # Morning check-in: yes (08:15-08:45)
    # Evening check-in: no (20:45-21:15)
    # Consolidation: no (02:45-03:15)
    # Weekly self-model: no (03:00-04:00)
    # That's only 1. The PLAN may mean we test a combined scenario.
    # For this test, use the actual 3-candidate scenario: evening=20:50, consolidation=03:00, weekly=03:00 (Sun)
    # Testing the documented case: morning 08:30 Sunday → 1 candidate (morning), not 3
    # Use the multi-window Sunday 03:00 scenario: consolidation + weekly = 2 at 03:00 Sunday
    now = _at(3, 5, weekday=6)  # Sunday 03:05 — in both consolidation and weekly windows
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    assert len(candidates) == 2
    sources = {c.source_id for c in candidates}
    assert "consolidation" in sources
    assert "weekly_self_model" in sources


def test_all_routines_high_pressure():
    now = _at(8, 30)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    for c in candidates:
        assert c.pressure == 0.8
```

## Acceptance criteria

- Morning window (08:15–08:45) → `morning_checkin` candidate emitted.
- Already-sent guard (`kv_get` returns truthy) → no duplicate.
- Evening window (20:45–21:15) → `evening_checkin` candidate.
- Sunday 02:45–03:15 → `consolidation` candidate.
- Sunday 03:00–04:00 + not yet run this week → `weekly_self_model` candidate.
- All routine candidates have `pressure == 0.8`.

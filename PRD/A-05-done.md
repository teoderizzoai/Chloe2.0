# A-05 · `actions/budget.py` — cost tracker

## Overview

Implement `chloe/actions/budget.py` with four functions: `charge(model, usage)` updates `budgets` rows; `exceeded_for(action) -> bool` checks against the configured cap; `throttle_level() -> float` returns the 0..1 used fraction; `reset_windows()` zeroes expired windows (called by a daily cron).

## Context

The current 1.0 codebase has a `MIN_SECONDS_BETWEEN_AUTONOMOUS_EVENTS` constant as the only cost control. 2.0 introduces USD-denominated budgets across three time windows (today, this_hour, this_week) so that Teo can cap spending explicitly. The budget is checked by the gate before deliberation — if exceeded, the action is self-aborted without an LLM call.

## Gemini pricing constants

```python
# Approximate prices as of 2026 (update as needed)
PRICE_PER_1M_TOKENS = {
    "gemini-2.5-pro":   {"input": 3.50, "output": 10.50, "thinking": 3.50},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60,  "thinking": 0.10},
}

def price_usd(model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    rates = PRICE_PER_1M_TOKENS.get(model, {"input": 1.0, "output": 1.0, "thinking": 1.0})
    return (
        input_tokens    * rates["input"]   / 1_000_000 +
        output_tokens   * rates["output"]  / 1_000_000 +
        thinking_tokens * rates["thinking"]/ 1_000_000
    )
```

## Interface

```python
from chloe.actions.schema import Action

def charge(model: str, usage: dict) -> None:
    """
    usage = {"input_tokens": int, "output_tokens": int, "thinking_tokens": int}
    Updates all three budget windows (today, this_hour, this_week).
    """
    ...

def exceeded_for(action: Action) -> bool:
    """
    Returns True if today.usd >= today.cap * 1.0.
    Uses preferences["spending_cap_usd_day"] as the cap.
    """
    ...

def throttle_level() -> float:
    """
    Returns today.usd / cap as a float in [0, 1].
    Used by the initiative engine to raise the selection threshold.
    """
    ...

def reset_windows() -> None:
    """
    Zero out any window whose reset_at < now. Update reset_at to the next period.
    Called at boot and by a daily background task.
    """
    ...
```

## Implementation

```python
# chloe/actions/budget.py

import json
from datetime import datetime, timezone, timedelta
from chloe.state.db import get_connection
from chloe.state import kv

def _get_cap() -> float:
    import json as _json
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM preferences WHERE key = 'spending_cap_usd_day'"
    ).fetchone()
    if row:
        return float(_json.loads(row[0]))
    return 1.50  # default

def charge(model: str, usage: dict) -> None:
    usd = price_usd(
        model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("thinking_tokens", 0),
    )
    tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    
    conn = get_connection()
    conn.execute(
        """
        UPDATE budgets
        SET usd = usd + ?, tokens = tokens + ?
        WHERE window IN ('today', 'this_hour', 'this_week')
        """,
        (usd, tokens),
    )
    conn.commit()
    
    # Also record via metrics
    from chloe.observability.metrics import record_llm_call
    record_llm_call(
        model, usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("thinking_tokens", 0), usd
    )

def exceeded_for(action: Action) -> bool:
    cap = _get_cap()
    conn = get_connection()
    row = conn.execute(
        "SELECT usd FROM budgets WHERE window = 'today'"
    ).fetchone()
    if not row:
        return False
    return row["usd"] >= cap

def throttle_level() -> float:
    cap = _get_cap()
    if cap <= 0:
        return 1.0
    conn = get_connection()
    row = conn.execute(
        "SELECT usd FROM budgets WHERE window = 'today'"
    ).fetchone()
    if not row:
        return 0.0
    return min(row["usd"] / cap, 1.0)

def reset_windows() -> None:
    now = datetime.now(timezone.utc)
    conn = get_connection()
    rows = conn.execute(
        "SELECT window, reset_at FROM budgets"
    ).fetchall()
    
    for row in rows:
        reset_at = datetime.fromisoformat(row["reset_at"]).replace(tzinfo=timezone.utc)
        if now >= reset_at:
            # Zero out and set next reset
            if row["window"] == "today":
                next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
            elif row["window"] == "this_hour":
                next_reset = (now + timedelta(hours=1)).replace(minute=0, second=0)
            else:  # this_week
                days_until_monday = (7 - now.weekday()) % 7 or 7
                next_reset = (now + timedelta(days=days_until_monday)).replace(
                    hour=0, minute=0, second=0
                )
            conn.execute(
                "UPDATE budgets SET usd=0, tokens=0, reset_at=? WHERE window=?",
                (next_reset.isoformat(), row["window"]),
            )
    conn.commit()
```

## Dependencies

- A-01 (`budgets` and `preferences` tables).
- A-02 (`Action` schema for `exceeded_for`).
- F-10 (metrics for `record_llm_call`).

## Testing

### Unit tests — `tests/unit/test_budget.py`

```python
import pytest
from pathlib import Path
from chloe.state.db import migrate, get_connection, close
from chloe.actions.schema import Action
from chloe.actions import budget

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

def make_action(auth="kinetic"):
    return Action(
        tool="spotify", verb="queue_track",
        intent="test", preview="test",
        authorization=auth,
    )

def test_charge_updates_today():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=0 WHERE window='today'")
    conn.commit()
    
    budget.charge("gemini-2.5-flash", {"input_tokens": 100, "output_tokens": 50})
    
    row = get_connection().execute(
        "SELECT usd FROM budgets WHERE window='today'"
    ).fetchone()
    assert row["usd"] > 0

def test_not_exceeded_below_cap():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=1.49 WHERE window='today'")
    conn.commit()
    assert not budget.exceeded_for(make_action())

def test_exceeded_at_cap():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=1.50 WHERE window='today'")
    conn.commit()
    assert budget.exceeded_for(make_action())

def test_exceeded_after_charge_pushes_over():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=1.49 WHERE window='today'")
    conn.commit()
    assert not budget.exceeded_for(make_action())
    # Charge $0.02 more
    budget.charge("gemini-2.5-pro", {"input_tokens": 1_000, "output_tokens": 2_000})
    assert budget.exceeded_for(make_action())

def test_throttle_level_proportional():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.00', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=0.50 WHERE window='today'")
    conn.commit()
    level = budget.throttle_level()
    assert 0.49 < level < 0.51

def test_throttle_level_zero_when_empty():
    conn = get_connection()
    conn.execute("UPDATE budgets SET usd=0 WHERE window='today'")
    conn.commit()
    assert budget.throttle_level() == pytest.approx(0.0)

def test_reset_windows_zeroes_expired(monkeypatch):
    from datetime import datetime, timezone
    conn = get_connection()
    # Set reset_at in the past
    past = "2020-01-01T00:00:00"
    conn.execute("UPDATE budgets SET usd=1.0, reset_at=? WHERE window='today'", (past,))
    conn.commit()
    budget.reset_windows()
    row = conn.execute("SELECT usd FROM budgets WHERE window='today'").fetchone()
    assert row["usd"] == 0.0
```

## Acceptance criteria

- Charge $1.49 against a $1.50 cap → `exceeded_for` returns `False`.
- Charge $0.02 more → `exceeded_for` returns `True`.
- `throttle_level()` returns proportional fraction.
- `reset_windows()` zeroes expired windows and updates `reset_at`.

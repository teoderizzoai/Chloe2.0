# D-02 · `should_deliberate()` heuristic

## Overview

Implement the `should_deliberate(action) -> bool` pure function from PRD §8.3. Five conditions, each a separate predicate. Property-test the compound: for any `kinetic-sensitive` action, `should_deliberate` always returns `True`.

## Context

Deliberation (D-01) is an LLM call — it costs latency and tokens. `should_deliberate()` is the cheap gatekeeper that decides whether to invoke the LLM at all. The five conditions are ordered from cheapest to most specific: auth class, budget, recency, cost, and repetition avoidance.

## Implementation

```python
# chloe/actions/deliberate.py  (add after deliberate() function)

from chloe.actions.budget import throttle_level as _throttle_level
from chloe.actions.audit import recent as _audit_recent
from datetime import datetime, timedelta


def should_deliberate(action) -> bool:
    """
    Return True if the gate should run a deliberation LLM call before executing.
    Cheap pure heuristic — no LLM, no DB writes.
    """
    return (
        _is_kinetic_sensitive(action)
        or _budget_near_cap()
        or _recent_outreach_spike()
        or _high_cost_estimate(action)
        or _repeated_same_verb(action)
    )


def _is_kinetic_sensitive(action) -> bool:
    return action.authorization == "kinetic-sensitive"


def _budget_near_cap() -> bool:
    return _throttle_level() > 0.75


def _recent_outreach_spike() -> bool:
    """True if more than 2 kinetic actions in the last 60 minutes."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    recent = _audit_recent(n=50)
    kinetic_recent = [
        a for a in recent
        if a.authorization in ("kinetic", "kinetic-sensitive")
        and a.proposed_at and a.proposed_at > cutoff.isoformat()
    ]
    return len(kinetic_recent) > 2


def _high_cost_estimate(action) -> bool:
    """True if the estimated cost exceeds $0.10."""
    if not action.cost_estimate:
        return False
    return action.cost_estimate.usd > 0.10


def _repeated_same_verb(action) -> bool:
    """True if the same tool+verb appeared 3+ times in the last 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent = _audit_recent(n=100)
    same_verb_count = sum(
        1 for a in recent
        if a.tool == action.tool and a.verb == action.verb
        and a.proposed_at and a.proposed_at > cutoff.isoformat()
    )
    return same_verb_count >= 3
```

## Dependencies

- A-05 (`budget.py` — `throttle_level()`).
- A-03 (`audit.py` — `recent(n)`).
- D-01 (`deliberate.py` — this function lives in the same module).

## Testing

### Unit tests — `tests/unit/test_should_deliberate.py`

```python
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from hypothesis import given, strategies as st
from chloe.actions.schema import Action
from chloe.actions.deliberate import (
    should_deliberate,
    _is_kinetic_sensitive,
    _budget_near_cap,
    _recent_outreach_spike,
    _high_cost_estimate,
    _repeated_same_verb,
)


def _make_action(**kwargs):
    defaults = dict(
        tool="spotify", verb="queue_track",
        args={}, intent="test", preview="test",
        authorization="kinetic",
    )
    defaults.update(kwargs)
    return Action(**defaults)


# --- Per-condition unit tests ---

def test_kinetic_sensitive_always_deliberate():
    a = _make_action(authorization="kinetic-sensitive")
    assert _is_kinetic_sensitive(a) is True


def test_kinetic_does_not_trigger_sensitive_check():
    a = _make_action(authorization="kinetic")
    assert _is_kinetic_sensitive(a) is False


def test_budget_near_cap(monkeypatch):
    monkeypatch.setattr("chloe.actions.deliberate._throttle_level", lambda: 0.8)
    assert _budget_near_cap() is True


def test_budget_under_threshold(monkeypatch):
    monkeypatch.setattr("chloe.actions.deliberate._throttle_level", lambda: 0.5)
    assert _budget_near_cap() is False


def test_recent_outreach_spike(monkeypatch):
    now = datetime.utcnow()
    fake_actions = [
        _make_action(authorization="kinetic")
        for _ in range(3)
    ]
    for a in fake_actions:
        a.proposed_at = (now - timedelta(minutes=10)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _recent_outreach_spike() is True


def test_no_outreach_spike(monkeypatch):
    now = datetime.utcnow()
    fake_actions = [_make_action(authorization="kinetic")]
    fake_actions[0].proposed_at = (now - timedelta(minutes=10)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _recent_outreach_spike() is False


def test_high_cost_estimate():
    a = _make_action()
    a.cost_estimate = MagicMock(usd=0.15)
    assert _high_cost_estimate(a) is True


def test_low_cost_estimate():
    a = _make_action()
    a.cost_estimate = MagicMock(usd=0.05)
    assert _high_cost_estimate(a) is False


def test_no_cost_estimate():
    a = _make_action()
    a.cost_estimate = None
    assert _high_cost_estimate(a) is False


def test_repeated_same_verb(monkeypatch):
    now = datetime.utcnow()
    a = _make_action(tool="notes", verb="append")
    fake_actions = [_make_action(tool="notes", verb="append") for _ in range(3)]
    for fa in fake_actions:
        fa.proposed_at = (now - timedelta(hours=1)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _repeated_same_verb(a) is True


def test_not_repeated(monkeypatch):
    now = datetime.utcnow()
    a = _make_action(tool="notes", verb="append")
    fake_actions = [_make_action(tool="notes", verb="append")]
    fake_actions[0].proposed_at = (now - timedelta(hours=1)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _repeated_same_verb(a) is False


# --- Compound property test ---

@given(st.builds(
    Action,
    tool=st.just("gmail"),
    verb=st.just("send_reply"),
    args=st.just({"draftId": "d1"}),
    intent=st.text(min_size=1, max_size=50),
    preview=st.text(min_size=1, max_size=50),
    authorization=st.just("kinetic-sensitive"),
))
def test_kinetic_sensitive_always_true(action):
    """Property: any kinetic-sensitive action must always trigger deliberation."""
    with patch("chloe.actions.deliberate._throttle_level", return_value=0.0), \
         patch("chloe.actions.deliberate._audit_recent", return_value=[]):
        assert should_deliberate(action) is True
```

## Acceptance criteria

- 6 unit tests pass: one per condition + the compound function.
- Hypothesis property test: for any `kinetic-sensitive` action, `should_deliberate` returns `True` regardless of other state.
- `should_deliberate` for a zero-budget, no-audit, low-cost, non-repeated `kinetic` action returns `False`.

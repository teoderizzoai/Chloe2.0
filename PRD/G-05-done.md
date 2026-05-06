# G-05 · Cost-outlier deliberation path

## Overview

Two changes to the deliberation path. First, in `should_deliberate()`: add a condition that returns `True` when `action.cost_estimate.usd > 0.10`. Second, in `deliberate.py`: when the action is also `kinetic-sensitive`, escalate the Flash call to `llm.pro_thinking()` with a small thinking budget instead of the standard Flash call.

## Context

Most actions are cheap (< $0.01 in Gemini costs). Occasional actions — long research tasks, multi-step calendar operations, expensive web search chains — can cost significantly more. The $0.10 threshold catches these outliers and forces deliberation. For high-cost kinetic-sensitive actions (send email, control home), the additional thinking budget is worth paying to avoid regrettable autonomous decisions. For high-cost non-kinetic actions, standard Flash deliberation is sufficient.

## Implementation

### `should_deliberate()` — add cost check

```python
# In chloe/actions/deliberate.py — update should_deliberate():

def should_deliberate(action: "Action") -> bool:
    """Return True if deliberation is required before executing this action."""
    return (
        _is_kinetic_sensitive(action)
        or _budget_near_cap(action)
        or _recent_outreach_spike(action)
        or _high_cost_estimate(action)          # G-05: new condition
        or _repeated_same_verb(action)
    )


def _high_cost_estimate(action: "Action") -> bool:
    cost = getattr(action, "cost_estimate", None)
    if cost is None:
        return False
    return cost.usd > 0.10
```

### `deliberate()` — escalate to pro_thinking for kinetic-sensitive + high-cost

```python
# In chloe/actions/deliberate.py — update deliberate():

from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import Verdict
from chloe.observability.logging import get_logger
from chloe.observability.metrics import deliberation_calls_total

log = get_logger("deliberate")

_gemini = GeminiClient()

DELIBERATION_THINKING_BUDGET = 512  # Tokens; calibrated in H-04


async def deliberate(action: "Action", context: dict) -> Verdict | None:
    """
    Returns Verdict with field 'decision' in {"proceed","abort","revise"}.
    Returns None (proceed) if LLM call fails.
    """
    input_pack = _build_input_pack(action, context)
    prompt_name = "deliberation.md"

    use_pro = _is_kinetic_sensitive(action) and _high_cost_estimate(action)

    try:
        if use_pro:
            log.info("deliberate_pro_thinking", action_id=action.id, cost_usd=action.cost_estimate.usd)
            deliberation_calls_total.labels(model="pro_thinking").inc()
            result = await _gemini.pro_thinking(
                prompt_name=prompt_name,
                payload=input_pack,
                schema=Verdict,
                thinking_budget=DELIBERATION_THINKING_BUDGET,
            )
        else:
            log.info("deliberate_flash", action_id=action.id)
            deliberation_calls_total.labels(model="flash").inc()
            result = await _gemini.flash(
                prompt_name=prompt_name,
                payload=input_pack,
                schema=Verdict,
            )
    except Exception as exc:
        log.warning("deliberate_llm_error", error=str(exc))
        return None

    if result is None:
        return None

    return Verdict(**result) if isinstance(result, dict) else result
```

### Prometheus counter

```python
# In chloe/observability/metrics.py — add:
from prometheus_client import Counter

deliberation_calls_total = Counter(
    "chloe_deliberation_calls_total",
    "Number of deliberation LLM calls",
    labelnames=["model"],  # "flash" or "pro_thinking"
)
```

### `Verdict` schema (already in F-06, confirm fields)

```python
# In chloe/llm/schemas.py (verify/update):
from pydantic import BaseModel, Field
from typing import Literal

class Verdict(BaseModel):
    decision: Literal["proceed", "abort", "revise"]
    rationale: str = Field(max_length=500)
    revised_args: dict | None = None  # Only for "revise" decisions
```

## Testing

### Unit tests — `tests/unit/test_deliberation_escalation.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.actions.deliberate import should_deliberate, deliberate, _high_cost_estimate


class MockCostEstimate:
    def __init__(self, usd: float):
        self.usd = usd


class MockAction:
    def __init__(self, auth_class="kinetic", cost_usd=0.0, tool="notes", verb="append"):
        self.id = "test_action_id"
        self.tool = tool
        self.verb = verb
        self.auth_class = auth_class
        self.cost_estimate = MockCostEstimate(cost_usd)
        # Other fields used by should_deliberate:
        self._recent_kinetic_count = 0
        self._verb_count_24h = 0


def test_high_cost_triggers_deliberation():
    action = MockAction(cost_usd=0.15)
    assert should_deliberate(action)


def test_low_cost_does_not_trigger_by_cost_alone():
    action = MockAction(cost_usd=0.05)
    # Only cost condition; kinetic-sensitive and others not met
    assert not _high_cost_estimate(action)


def test_cost_threshold_boundary():
    assert _high_cost_estimate(MockAction(cost_usd=0.10)) is False  # Not > 0.10
    assert _high_cost_estimate(MockAction(cost_usd=0.101)) is True


@pytest.mark.asyncio
async def test_kinetic_sensitive_high_cost_uses_pro_thinking():
    """High-cost kinetic-sensitive → pro_thinking called."""
    action = MockAction(auth_class="kinetic-sensitive", cost_usd=0.15)
    context = {}

    pro_called = []
    flash_called = []

    with patch("chloe.actions.deliberate._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(
            side_effect=lambda **kw: pro_called.append(kw) or {"decision": "proceed", "rationale": "ok"}
        )
        mock_gemini.flash = AsyncMock(
            side_effect=lambda **kw: flash_called.append(kw) or {"decision": "proceed", "rationale": "ok"}
        )
        result = await deliberate(action, context)

    assert result is not None
    assert result.decision == "proceed"
    assert len(pro_called) == 1
    assert len(flash_called) == 0


@pytest.mark.asyncio
async def test_kinetic_high_cost_uses_flash():
    """High-cost but NOT kinetic-sensitive → standard Flash (not pro_thinking)."""
    action = MockAction(auth_class="kinetic", cost_usd=0.15)
    context = {}

    pro_called = []
    flash_called = []

    with patch("chloe.actions.deliberate._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(
            side_effect=lambda **kw: flash_called.append(kw) or {"decision": "proceed", "rationale": "ok"}
        )
        mock_gemini.pro_thinking = AsyncMock(
            side_effect=lambda **kw: pro_called.append(kw) or {"decision": "proceed", "rationale": "ok"}
        )
        result = await deliberate(action, context)

    assert len(flash_called) == 1
    assert len(pro_called) == 0


@pytest.mark.asyncio
async def test_kinetic_sensitive_low_cost_uses_flash():
    """kinetic-sensitive but low cost → Flash (not pro_thinking)."""
    action = MockAction(auth_class="kinetic-sensitive", cost_usd=0.02)
    context = {}

    pro_called = []
    flash_called = []

    with patch("chloe.actions.deliberate._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(
            side_effect=lambda **kw: flash_called.append(kw) or {"decision": "proceed", "rationale": "ok"}
        )
        mock_gemini.pro_thinking = AsyncMock(
            side_effect=lambda **kw: pro_called.append(kw)
        )
        result = await deliberate(action, context)

    assert len(flash_called) == 1
    assert len(pro_called) == 0


@pytest.mark.asyncio
async def test_llm_failure_returns_none_proceed():
    """LLM error → None (proceed) to avoid blocking Chloe."""
    action = MockAction(cost_usd=0.01)
    context = {}

    with patch("chloe.actions.deliberate._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(side_effect=Exception("LLM timeout"))
        result = await deliberate(action, context)

    assert result is None
```

### Property test

```python
from hypothesis import given, strategies as st

@given(st.floats(min_value=0.101, max_value=100.0))
def test_high_cost_always_triggers_deliberation(cost):
    from chloe.actions.deliberate import _high_cost_estimate
    action = MockAction(cost_usd=cost)
    assert _high_cost_estimate(action) is True


@given(st.floats(min_value=0.0, max_value=0.1))
def test_low_cost_never_triggers_by_cost(cost):
    from chloe.actions.deliberate import _high_cost_estimate
    action = MockAction(cost_usd=cost)
    assert _high_cost_estimate(action) is False
```

## Dependencies

- D-01 (`deliberate.py` — base deliberation logic).
- D-02 (`should_deliberate()` — heuristic conditions).
- F-05 (`llm/gemini.py` — `pro_thinking()` method).
- G-06 (`cost_estimate` populated on actions).

## Acceptance criteria

- `should_deliberate()` returns `True` when `cost_estimate.usd > 0.10`.
- High-cost + kinetic-sensitive → `pro_thinking()` called with `thinking_budget=512`.
- High-cost + non-kinetic-sensitive → standard `flash()` called.
- Low-cost + kinetic-sensitive → standard `flash()` called.
- LLM failure → `None` returned (fail-open).
- `chloe_deliberation_calls_total` counter incremented with correct `model` label.

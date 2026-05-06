# G-06 · Spending-aware tool cost estimates

## Overview

Populate `cost_per_call_usd` for all registered tools. Add `cost_estimate` calculation to `realize(candidate) -> Action` in the initiative engine. Log `cost_usd` to the `actions` table. Feed `cost_usd` into `budget.charge()` after each successful execution.

## Context

Without real cost estimates, the budget throttle is a rough heuristic based on action count. With this step, each action carries a real USD estimate that tracks against Gemini billing, API fees, and downstream service costs. The initiative engine can then make spending-aware decisions (D-11) and G-05's cost-outlier deliberation path has real numbers to work with.

## Implementation

### Tool cost registry

```python
# chloe/tools/costs.py

from dataclasses import dataclass

@dataclass
class CostEstimate:
    usd: float
    breakdown: dict[str, float]  # {"gemini_flash": 0.002, "gmail_api": 0.0}

# Per-tool, per-verb cost estimates (USD, approximate)
TOOL_COSTS: dict[str, dict[str, CostEstimate]] = {
    "notes": {
        "append": CostEstimate(usd=0.001, breakdown={"gemini_flash": 0.001}),
        "search": CostEstimate(usd=0.002, breakdown={"gemini_flash": 0.002}),
        "revert": CostEstimate(usd=0.0005, breakdown={}),
    },
    "web_search": {
        "search": CostEstimate(usd=0.005, breakdown={"serp_api": 0.005}),
    },
    "spotify": {
        "play_track": CostEstimate(usd=0.0, breakdown={}),
        "add_to_queue": CostEstimate(usd=0.0, breakdown={}),
        "play_playlist": CostEstimate(usd=0.0, breakdown={}),
        "set_volume": CostEstimate(usd=0.0, breakdown={}),
        "pause": CostEstimate(usd=0.0, breakdown={}),
        "resume": CostEstimate(usd=0.0, breakdown={}),
        "skip": CostEstimate(usd=0.0, breakdown={}),
        "clear_queue": CostEstimate(usd=0.0, breakdown={}),
    },
    "gmail": {
        "list_threads": CostEstimate(usd=0.001, breakdown={"gemini_flash": 0.001}),
        "read_thread": CostEstimate(usd=0.003, breakdown={"gemini_flash": 0.003}),
        "draft_reply": CostEstimate(usd=0.008, breakdown={"gemini_flash": 0.008}),
        "send_reply": CostEstimate(usd=0.008, breakdown={"gemini_flash": 0.008}),
    },
    "calendar": {
        "list_events": CostEstimate(usd=0.001, breakdown={}),
        "add_reminder": CostEstimate(usd=0.002, breakdown={"gemini_flash": 0.002}),
        "delete_event": CostEstimate(usd=0.0005, breakdown={}),
    },
    "smart_home": {
        "lights": CostEstimate(usd=0.0, breakdown={}),
        "thermostat": CostEstimate(usd=0.0, breakdown={}),
        "media_player": CostEstimate(usd=0.0, breakdown={}),
        "scene": CostEstimate(usd=0.0, breakdown={}),
    },
    "messages": {
        "send_text": CostEstimate(usd=0.001, breakdown={"push_apns": 0.0, "gemini_flash": 0.001}),
    },
    "weather": {
        "current": CostEstimate(usd=0.0, breakdown={"open_meteo": 0.0}),
        "forecast": CostEstimate(usd=0.0, breakdown={"open_meteo": 0.0}),
    },
    "maps": {
        "find_place": CostEstimate(usd=0.005, breakdown={"google_maps": 0.005}),
        "directions": CostEstimate(usd=0.01, breakdown={"google_maps": 0.01}),
        "traffic_to": CostEstimate(usd=0.01, breakdown={"google_maps": 0.01}),
        "commute_estimate": CostEstimate(usd=0.01, breakdown={"google_maps": 0.01}),
    },
    "self_tools": {
        "set_quiet": CostEstimate(usd=0.0, breakdown={}),
        "set_focus": CostEstimate(usd=0.0, breakdown={}),
        "add_goal": CostEstimate(usd=0.0, breakdown={}),
        "add_want": CostEstimate(usd=0.0, breakdown={}),
        "update_preference": CostEstimate(usd=0.0, breakdown={}),
        "archive_trait": CostEstimate(usd=0.0, breakdown={}),
    },
}

_DEFAULT_COST = CostEstimate(usd=0.002, breakdown={"default": 0.002})


def get_cost_estimate(tool: str, verb: str) -> CostEstimate:
    return TOOL_COSTS.get(tool, {}).get(verb, _DEFAULT_COST)
```

### Wire into `realize()` in the initiative engine

```python
# In chloe/initiative/engine.py — update realize():

from chloe.tools.costs import get_cost_estimate

def realize(candidate: "CandidateAction") -> "Action":
    from chloe.actions.models import Action
    from chloe.tools.registry import ToolRegistry
    registry = ToolRegistry.instance()

    tool_def = registry.get_tool(candidate.tool)
    verb_def = tool_def.verbs.get(candidate.verb) if tool_def else None
    auth_class = verb_def.auth_class if verb_def else "kinetic"

    cost_estimate = get_cost_estimate(candidate.tool, candidate.verb)

    return Action(
        tool=candidate.tool,
        verb=candidate.verb,
        args=candidate.args,
        intent=candidate.intent,
        auth_class=auth_class,
        cost_estimate=cost_estimate,        # G-06: attach estimate
        source=candidate.source,
    )
```

### `actions` table migration — add `cost_usd` column

```sql
-- chloe/state/migrations/0006_action_cost.sql

ALTER TABLE actions ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0.0;
```

### `Action` model update

```python
# In chloe/actions/models.py:

from chloe.tools.costs import CostEstimate

class Action(BaseModel):
    # ... existing fields ...
    cost_estimate: CostEstimate | None = None

    @property
    def cost_usd(self) -> float:
        return self.cost_estimate.usd if self.cost_estimate else 0.0
```

### Wire `cost_usd` into `budget.charge()` in `gate.py`

```python
# In chloe/actions/gate.py — _execute_and_record():

async def _execute_and_record(self, action: Action) -> ToolResult:
    result = await self._tool_registry.execute(action.tool, action.verb, action.args)

    if result.success:
        # Charge budget with real cost estimate
        await budget.charge(
            usd=action.cost_usd,
            action_id=action.id,
        )

    # Persist cost_usd to actions table
    conn = get_connection()
    conn.execute(
        "UPDATE actions SET cost_usd=? WHERE id=?",
        (action.cost_usd, action.id),
    )
    conn.commit()

    return result
```

### Update `budget.charge()` signature

```python
# In chloe/actions/budget.py:

async def charge(usd: float, action_id: str) -> None:
    """Record a real USD charge against today's budget."""
    conn = get_connection()
    today = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO budgets (date, usd_charged, action_count)
        VALUES (?, ?, 1)
        ON CONFLICT(date) DO UPDATE SET
            usd_charged = usd_charged + excluded.usd_charged,
            action_count = action_count + 1
        """,
        (today, usd),
    )
    conn.commit()
```

## Testing

### Unit tests — `tests/unit/test_tool_costs.py`

```python
import pytest
from chloe.tools.costs import get_cost_estimate, TOOL_COSTS, _DEFAULT_COST


def test_known_tool_verb_returns_estimate():
    cost = get_cost_estimate("gmail", "send_reply")
    assert cost.usd == 0.008
    assert "gemini_flash" in cost.breakdown


def test_unknown_tool_returns_default():
    cost = get_cost_estimate("nonexistent_tool", "some_verb")
    assert cost.usd == _DEFAULT_COST.usd


def test_unknown_verb_returns_default():
    cost = get_cost_estimate("gmail", "nonexistent_verb")
    assert cost.usd == _DEFAULT_COST.usd


def test_free_tools_have_zero_cost():
    for verb in TOOL_COSTS.get("spotify", {}).values():
        assert verb.usd == 0.0


def test_all_tools_have_non_negative_costs():
    for tool, verbs in TOOL_COSTS.items():
        for verb_name, cost in verbs.items():
            assert cost.usd >= 0.0, f"{tool}.{verb_name} has negative cost"


def test_high_cost_tools_identified():
    """Tools that might trigger G-05 deliberation."""
    gmail_send = get_cost_estimate("gmail", "send_reply")
    assert gmail_send.usd < 0.10  # Should NOT trigger by default
    # Note: very expensive research chains would be aggregated across multiple calls
```

### Integration test — budget tracking after 24h

```python
# tests/integration/test_budget_tracking.py
import pytest

@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_charges_after_action():
    from chloe.actions.budget import charge, get_today_spend
    from datetime import date

    initial_spend = await get_today_spend()
    await charge(usd=0.005, action_id="test_action_001")
    final_spend = await get_today_spend()

    assert abs(final_spend - initial_spend - 0.005) < 0.0001


@pytest.mark.integration
@pytest.mark.asyncio
async def test_realize_attaches_cost_estimate():
    from chloe.initiative.engine import realize
    from chloe.initiative.candidates import CandidateAction

    candidate = CandidateAction(
        tool="gmail",
        verb="draft_reply",
        args={},
        intent="Reply to email",
        source="test",
        pressure=0.5,
    )
    action = realize(candidate)
    assert action.cost_estimate is not None
    assert action.cost_usd == 0.008
```

### Smoke test — non-zero budget after production run

After 24h in production:

```bash
# Query actions table for total cost today:
sqlite3 chloe.db "SELECT SUM(cost_usd) FROM actions WHERE date(created_at)=date('now')"
# Expected: non-zero value matching approximate Gemini spend
```

## Dependencies

- D-08 (`realize()` in `initiative/engine.py`).
- A-06 (`budget.py` — `charge()`, `get_today_spend()`).
- G-05 (`_high_cost_estimate()` — consumes `cost_estimate.usd`).
- `0006_action_cost.sql` migration.

## Acceptance criteria

- `get_cost_estimate(tool, verb)` returns correct `CostEstimate` for all known tool/verb combos.
- Unknown tool/verb → `_DEFAULT_COST` returned (never raises).
- `realize()` attaches `cost_estimate` to the returned `Action`.
- `cost_usd` column populated in `actions` table after each execution.
- `budget.charge()` called with real `usd` after successful execution.
- After 24h in production, `budgets.today.usd_charged` is non-zero.

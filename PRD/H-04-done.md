# H-04 · Thinking-budget calibration

## Overview

Run the weekly self-model on 3 past DB snapshots at thinking budgets 1024, 4096, and 8192. Compare output quality (read by Teo). Record the chosen value in `WEEKLY_PARAMS` with a comment. Repeat the same process for deliberation (target: feels considered, not verbose).

## Context

Thinking budget is a cost/quality tradeoff. Too low and the output is shallow; too high and it adds cost without improvement. The calibration must be done against real data (not synthetic) and judged by Teo, not automated metrics. The result is a comment in the code explaining why a specific budget was chosen — future maintainers can re-run calibration if model behavior changes.

## Calibration Procedure

### Step 1 — Export 3 past identity snapshots

```python
# scripts/export_identity_snapshot.py
"""
Export an identity snapshot from the DB at a given point in time.
Run three times for different dates to get calibration inputs.
"""
import sys
from chloe.identity.self_model import _assemble_input_pack
from chloe.state.db import get_connection
import json

def export_snapshot(output_path: str):
    pack = _assemble_input_pack()
    with open(output_path, "w") as f:
        json.dump(pack, f, indent=2, default=str)
    print(f"Exported to {output_path}")

if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else "snapshot.json"
    export_snapshot(output)
```

Run on 3 separate days (or use `git stash`/time-machine approach):

```bash
python scripts/export_identity_snapshot.py snapshots/snap_week1.json
python scripts/export_identity_snapshot.py snapshots/snap_week2.json
python scripts/export_identity_snapshot.py snapshots/snap_week3.json
```

### Step 2 — Run self-model at each budget

```python
# scripts/calibrate_thinking_budget.py
"""
Run weekly self-model on a snapshot at multiple thinking budgets.
Compare outputs side-by-side.
"""
import asyncio
import json
import sys
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import SelfModelOutput

BUDGETS = [1024, 4096, 8192]
_gemini = GeminiClient()


async def run_at_budget(snapshot_path: str, budget: int) -> dict | None:
    with open(snapshot_path) as f:
        payload = json.load(f)

    result = await _gemini.pro_thinking(
        prompt_name="weekly_self_model.md",
        payload=payload,
        schema=SelfModelOutput,
        thinking_budget=budget,
    )
    return result


async def calibrate(snapshot_path: str):
    results = {}
    for budget in BUDGETS:
        print(f"\n{'='*60}")
        print(f"Budget: {budget} tokens")
        print('='*60)
        result = await run_at_budget(snapshot_path, budget)
        if result:
            output = SelfModelOutput(**result) if isinstance(result, dict) else result
            print(f"Belief: {output.self_narrative_belief}")
            print(f"Intention: {output.next_week_intention}")
            print(f"Contradictions: {output.noted_contradictions}")
            results[budget] = result
        else:
            print("(Failed)")

    # Save all outputs for side-by-side review
    with open("calibration_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nSaved to calibration_results.json")


if __name__ == "__main__":
    snap = sys.argv[1] if len(sys.argv) > 1 else "snapshots/snap_week1.json"
    asyncio.run(calibrate(snap))
```

### Step 3 — Repeat for deliberation

```python
# scripts/calibrate_deliberation_budget.py
"""
Run deliberation at multiple budgets for a known kinetic-sensitive action.
"""
import asyncio
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import Verdict

DELIBERATION_BUDGETS = [256, 512, 1024]
_gemini = GeminiClient()

SAMPLE_INPUT = {
    "proposed_action": {
        "tool": "gmail",
        "verb": "send_reply",
        "args": {"draftId": "draft_test_001"},
        "intent": "Send reply to Alice's email about the project deadline",
        "auth_class": "kinetic-sensitive",
        "cost_usd": 0.008,
    },
    "procedural_hits": [],
    "recent_audit": "No recent kinetic actions.",
    "budget_throttle": 0.3,
    "time_of_day": "14:30",
    "day_of_week": "Monday",
    "last_chat_seen": "10 minutes ago",
}


async def calibrate_deliberation():
    for budget in DELIBERATION_BUDGETS:
        print(f"\nBudget: {budget}")
        result = await _gemini.pro_thinking(
            prompt_name="deliberation.md",
            payload=SAMPLE_INPUT,
            schema=Verdict,
            thinking_budget=budget,
        )
        if result:
            v = Verdict(**result) if isinstance(result, dict) else result
            print(f"  Decision: {v.decision}")
            print(f"  Rationale: {v.rationale}")
        else:
            print("  (Failed)")


if __name__ == "__main__":
    asyncio.run(calibrate_deliberation())
```

### Step 4 — Record chosen values

After Teo reviews the outputs and selects budgets:

```python
# In chloe/identity/self_model.py — update WEEKLY_PARAMS:

WEEKLY_PARAMS = {
    "thinking_config": {
        # Calibrated 2026-05-04 against 3 production snapshots.
        # 1024: shallow, missed key goal contradictions
        # 4096: good quality, but dropped nuance about affect drift
        # 8192: full context used; belief was more specific and honest
        # Chosen: 8192. Cost ~$0.08/week; acceptable.
        "thinking_budget": 8192,
    }
}

# In chloe/actions/deliberate.py:
# Calibrated 2026-05-04 against kinetic-sensitive email send scenarios.
# 256: too brief, rationale often generic
# 512: specific rationale, catches procedural rule violations
# 1024: no meaningful improvement over 512, higher cost
# Chosen: 512.
DELIBERATION_THINKING_BUDGET = 512
```

## Testing

### Unit test — constants are commented

```python
# tests/unit/test_thinking_budget_calibration.py
import inspect
from chloe.identity.self_model import WEEKLY_PARAMS
from chloe.actions.deliberate import DELIBERATION_THINKING_BUDGET


def test_weekly_params_has_thinking_budget():
    assert "thinking_budget" in WEEKLY_PARAMS["thinking_config"]
    assert WEEKLY_PARAMS["thinking_config"]["thinking_budget"] > 0


def test_deliberation_thinking_budget_is_positive():
    assert DELIBERATION_THINKING_BUDGET > 0


def test_weekly_params_comment_exists():
    """Verify the WEEKLY_PARAMS comment exists in source (calibration rationale)."""
    import chloe.identity.self_model as m
    src = inspect.getsource(m)
    assert "Calibrated" in src or "calibrat" in src.lower()


def test_deliberation_budget_in_range():
    """Deliberation budget should be 256-2048; too high = verbose, too low = shallow."""
    assert 256 <= DELIBERATION_THINKING_BUDGET <= 2048
```

### Manual review checklist

After running calibration scripts:

```
[ ] self-model at 1024: belief is non-trivial (not generic "I was helpful")
[ ] self-model at 4096: adds nuance; catches at least one contradiction
[ ] self-model at 8192: most specific; references actual events from actions
[ ] Deliberation at 256: rationale is specific, not "this seems reasonable"
[ ] Deliberation at 512: correctly cites procedural rules when present
[ ] Deliberation at 1024: no noticeable quality improvement over 512
[ ] Chosen budgets recorded in WEEKLY_PARAMS and DELIBERATION_THINKING_BUDGET
[ ] Calibration date recorded in comments
```

## Dependencies

- H-03 (`identity/self_model.py` — `WEEKLY_PARAMS`).
- D-01 (`deliberate.py` — `DELIBERATION_THINKING_BUDGET`).
- F-05 (`llm/gemini.py` — `pro_thinking()` supports `thinking_budget` param).

## Acceptance criteria

- `WEEKLY_PARAMS["thinking_config"]["thinking_budget"]` has a calibration rationale comment.
- `DELIBERATION_THINKING_BUDGET` has a calibration rationale comment.
- Comments include the date of calibration and brief comparison of tested values.
- Both values pass range assertions in unit tests.
- Manual review checklist signed off by Teo.

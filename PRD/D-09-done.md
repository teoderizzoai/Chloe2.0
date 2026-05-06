# D-09 · Shadow runner — compare old vs new engine

## Overview

Wire the new initiative engine to run alongside 1.0's `_fire_event` for 2 weeks. New engine evaluates each tick but logs its decision to `kv["shadow_decisions"]` without executing. Old engine still drives actual actions. Expose `/admin/shadow` endpoint showing a side-by-side comparison.

## Context

Before cutting over to the new initiative engine, shadow mode validates that it produces sensible decisions in production conditions. Two weeks of shadow data provides enough coverage across daily routines, mood variations, and workweek patterns to tune the threshold before removing the old engine.

## Implementation

### Shadow runner

```python
# chloe/initiative/shadow.py

import json
from datetime import datetime
from chloe.initiative.engine import tick as new_tick
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("initiative.shadow")

MAX_SHADOW_RECORDS = 2000  # Keep last N decisions to avoid unbounded growth


async def shadow_tick() -> None:
    """
    Run the new initiative engine in shadow mode.
    Logs what the new engine WOULD have done without actually calling gate.submit.
    """
    from unittest.mock import AsyncMock, patch

    shadow_gate_calls = []

    async def shadow_gate(action):
        shadow_gate_calls.append({
            "tool": action.tool,
            "verb": action.verb,
            "intent": action.intent,
            "authorization": action.authorization,
        })
        # Return a fake success without executing
        from chloe.actions.schema import ActionResult
        return ActionResult(executed=False, suppressed=False)

    try:
        with patch("chloe.initiative.engine.gate_submit", new=shadow_gate):
            await new_tick()
    except Exception as exc:
        log.warning("shadow_tick_error", error=str(exc))
        return

    shadow_decision = {
        "timestamp": datetime.utcnow().isoformat(),
        "proposed": shadow_gate_calls[0] if shadow_gate_calls else None,
        "was_idle": len(shadow_gate_calls) == 0,
    }

    records = kv_get("shadow_decisions", default=[])
    records.append(shadow_decision)
    # Keep only the most recent records
    records = records[-MAX_SHADOW_RECORDS:]
    kv_set("shadow_decisions", records)

    if shadow_decision["proposed"]:
        log.info("shadow_would_have",
                 tool=shadow_decision["proposed"]["tool"],
                 verb=shadow_decision["proposed"]["verb"])
    else:
        log.debug("shadow_idle")
```

### Wire into background task loop

```python
# In loop.py: run shadow_tick alongside the old fire_event
# (Pseudocode — adapt to actual loop structure)

import asyncio
from chloe.initiative.shadow import shadow_tick

async def initiative_loop():
    while True:
        # 1. Old engine tick (actual execution)
        try:
            await _fire_event()  # 1.0 engine
        except Exception as e:
            log.warning("old_engine_error", error=str(e))

        # 2. New engine shadow tick (no execution)
        try:
            await shadow_tick()
        except Exception as e:
            log.warning("shadow_tick_error", error=str(e))

        await asyncio.sleep(60)  # 1-minute tick interval
```

### Admin endpoint

```python
# chloe/admin/shadow_routes.py

from fastapi import APIRouter
from chloe.state.kv import get as kv_get
from datetime import datetime

router = APIRouter(prefix="/admin/shadow", tags=["admin"])


@router.get("")
async def shadow_comparison():
    """Side-by-side comparison of old vs new engine decisions."""
    records = kv_get("shadow_decisions", default=[])

    # Aggregate stats
    total = len(records)
    new_idle = sum(1 for r in records if r.get("was_idle"))
    new_proposed = total - new_idle

    by_tool = {}
    for r in records:
        if r.get("proposed"):
            tool = r["proposed"].get("tool", "unknown")
            by_tool[tool] = by_tool.get(tool, 0) + 1

    recent = records[-50:]  # Last 50 for display

    return {
        "summary": {
            "total_ticks": total,
            "new_engine_idle": new_idle,
            "new_engine_proposed": new_proposed,
            "idle_rate": round(new_idle / total, 3) if total else 0,
            "by_tool": by_tool,
        },
        "recent_decisions": recent,
        "generated_at": datetime.utcnow().isoformat(),
    }
```

Register in `app.py`:

```python
from chloe.admin.shadow_routes import router as shadow_router
app.include_router(shadow_router)
```

## Dependencies

- D-08 (`initiative/engine.py` — `tick()` function).
- F-08 (`state/kv.py` — shadow log storage).
- F-11 (`app.py` and `loop.py` — wiring point).

## Testing

### Unit tests — `tests/unit/test_shadow.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.initiative.shadow import shadow_tick


@pytest.mark.asyncio
async def test_shadow_tick_logs_without_executing(monkeypatch):
    saved = {}

    def mock_kv_set(key, val):
        saved[key] = val

    def mock_kv_get(key, **kw):
        return kw.get("default")

    monkeypatch.setattr("chloe.initiative.shadow.kv_get", mock_kv_get)
    monkeypatch.setattr("chloe.initiative.shadow.kv_set", mock_kv_set)

    real_gate_calls = []

    from chloe.actions.schema import Action
    async def tracking_tick():
        # Simulate the engine proposing an action
        from chloe.initiative.engine import gate_submit
        from chloe.actions.schema import Action
        # Call the patched gate (which is the shadow gate)
        action = Action(tool="messages", verb="send_text", args={},
                       intent="Morning check-in", preview="Morning check-in",
                       authorization="kinetic")
        await gate_submit(action)

    monkeypatch.setattr("chloe.initiative.shadow.new_tick", tracking_tick)

    await shadow_tick()

    assert "shadow_decisions" in saved
    records = saved["shadow_decisions"]
    assert len(records) == 1
    assert records[0]["proposed"]["tool"] == "messages"


@pytest.mark.asyncio
async def test_shadow_tick_idle_logs_correctly(monkeypatch):
    saved = {}
    monkeypatch.setattr("chloe.initiative.shadow.kv_get", lambda k, **kw: kw.get("default"))
    monkeypatch.setattr("chloe.initiative.shadow.kv_set", lambda k, v: saved.update({k: v}))

    # Simulate idle tick (engine proposes nothing)
    async def idle_tick():
        pass

    monkeypatch.setattr("chloe.initiative.shadow.new_tick", idle_tick)

    await shadow_tick()
    records = saved.get("shadow_decisions", [])
    assert records[0]["was_idle"] is True


@pytest.mark.asyncio
async def test_shadow_endpoint_returns_summary():
    from httpx import AsyncClient
    from chloe.app import create_app
    from chloe.state.kv import set as kv_set

    kv_set("shadow_decisions", [
        {"timestamp": "2026-05-04T10:00:00", "proposed": {"tool": "messages", "verb": "send_text", "intent": "check in", "authorization": "kinetic"}, "was_idle": False},
        {"timestamp": "2026-05-04T10:01:00", "proposed": None, "was_idle": True},
    ])

    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/admin/shadow")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_ticks"] == 2
    assert data["summary"]["new_engine_idle"] == 1
    assert "messages" in data["summary"]["by_tool"]
```

## Acceptance criteria

- `shadow_tick()` runs the new engine without calling the real `gate.submit`.
- Proposed actions logged to `kv["shadow_decisions"]` with timestamp and tool/verb.
- Idle ticks logged with `was_idle: True`.
- `GET /admin/shadow` returns summary with `total_ticks`, `idle_rate`, `by_tool`.
- Shadow log capped at 2000 records (old records evicted).

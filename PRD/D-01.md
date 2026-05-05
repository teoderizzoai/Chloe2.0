# D-01 · `actions/deliberate.py` — deliberation Flash call

## Overview

Implement `deliberate(action, context) -> Verdict`. Builds the input pack from PRD §8.4, calls `llm.flash("deliberate_action.md", payload, schema=Verdict)`. Validates response against `Verdict` schema. Handles `None` from Gemini (treat as `proceed`). Wires into `gate.py` replacing the stub `should_proceed = True` logic.

## Context

Phase A shipped a gate stub that always proceeds. Deliberation is the LLM's chance to say "wait, this doesn't feel right" before an action executes. The Verdict schema (`decision: proceed | abort | revise`, `reason: str`) is already defined in `F-06`. The input pack includes recent audit context, budget state, time of day, and the proposed action — enough for Flash to make a grounded judgment without needing the full Pro model.

## Implementation

```python
# chloe/actions/deliberate.py

from chloe.llm.gemini import get_client as get_llm
from chloe.llm.schemas import Verdict
from chloe.actions.audit import recent as audit_recent, feed_text
from chloe.actions.budget import throttle_level
from chloe.state.kv import get as kv_get
from chloe.observability.logging import get_logger
from datetime import datetime
import json

log = get_logger("deliberate")


async def deliberate(action, context: dict | None = None) -> Verdict | None:
    """
    Run a Flash deliberation call for the proposed action.
    Returns Verdict or None (treat as proceed) on LLM failure.
    """
    context = context or {}
    recent_actions = audit_recent(n=10)
    audit_feed = feed_text(recent_actions, n=10)

    payload = {
        "proposed_action": {
            "tool": action.tool,
            "verb": action.verb,
            "args": action.args,
            "intent": action.intent,
            "preview": action.preview,
            "auth_class": action.authorization,
        },
        "recent_audit": audit_feed,
        "budget_throttle": throttle_level(),
        "time_of_day": datetime.now().strftime("%H:%M"),
        "day_of_week": datetime.now().strftime("%A"),
        "context": context,
        "last_chat_seen": kv_get("last_chat_seen", default="unknown"),
    }

    llm = get_llm()
    result = await llm.flash("deliberate_action.md", payload, schema=Verdict)
    if result is None:
        log.warning("deliberation_llm_failed", action_id=action.id)
        return None  # Caller treats as proceed

    try:
        verdict = Verdict(**result) if isinstance(result, dict) else result
        log.info("deliberation_verdict",
                 action_id=action.id, decision=verdict.decision, reason=verdict.reason[:80])
        return verdict
    except Exception as exc:
        log.warning("deliberation_parse_error", error=str(exc))
        return None
```

## Update `gate.py`

```python
# In gate.py: replace stub deliberation

from chloe.actions.deliberate import deliberate
from chloe.actions.deliberate import should_deliberate

async def submit(action: Action) -> ActionResult:
    # ... leash check, budget check ...

    if should_deliberate(action):
        verdict = await deliberate(action)
        if verdict and verdict.decision == "abort":
            await _store_held_back_memory(action, reason=verdict.reason)
            conn.execute("UPDATE actions SET state='held_back' WHERE id=?", (action.id,))
            conn.commit()
            return ActionResult(
                executed=False, suppressed=True,
                reason=f"Deliberation: {verdict.reason}",
            )
        elif verdict and verdict.decision == "revise":
            # Log revision suggestion but proceed (initiative engine handles future revisions)
            log.info("deliberation_revise_suggestion", reason=verdict.reason)

    # ... rest of gate logic
```

## `deliberate_action.md` prompt template

```markdown
# Deliberate on proposed action

You are Chloe's inner deliberation voice. Evaluate whether the proposed action is appropriate right now.

## Proposed action
- Tool: {{proposed_action.tool}}
- Verb: {{proposed_action.verb}}
- Intent: {{proposed_action.intent}}
- Preview: {{proposed_action.preview}}
- Auth class: {{proposed_action.auth_class}}

## Recent actions (last 10)
{{recent_audit}}

## Context
- Time: {{time_of_day}} {{day_of_week}}
- Budget throttle: {{budget_throttle}} (0=free, 1=capped)
- Last chat with Teo: {{last_chat_seen}}

## Decision
Return JSON: {"decision": "proceed"|"abort"|"revise", "reason": "brief explanation"}

Abort if:
- Too many recent outreaches (>2 in last hour)
- Budget throttle > 0.9
- Action conflicts with recent explicit denials
- Action touches a sensitive area without clear context

Default to proceed if unsure.
```

## Dependencies

- A-08 (`gate.py` — deliberation stub to replace).
- D-02 (`should_deliberate()` — imported from `deliberate.py` or a sibling module).
- F-05 (`llm/gemini.py` — Flash call).
- F-06 (`llm/schemas.py` — `Verdict` schema).
- A-03 (`audit.py` — `recent()`, `feed_text()`).
- A-05 (`budget.py` — `throttle_level()`).

## Testing

### Unit tests — `tests/unit/test_deliberate.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.llm.schemas import Verdict

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def _make_action(**kwargs):
    defaults = dict(
        tool="spotify", verb="queue_track",
        args={"uri": "spotify:track:x"},
        intent="Queue a track",
        preview="Queue track",
        authorization="kinetic",
    )
    defaults.update(kwargs)
    return Action(**defaults)


@pytest.mark.asyncio
async def test_deliberate_proceed(monkeypatch):
    mock_flash = AsyncMock(return_value={"decision": "proceed", "reason": "Seems fine"})
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))

    from chloe.actions.deliberate import deliberate
    action = _make_action()
    verdict = await deliberate(action)

    assert verdict is not None
    assert verdict.decision == "proceed"


@pytest.mark.asyncio
async def test_deliberate_abort(monkeypatch, db):
    mock_flash = AsyncMock(return_value={"decision": "abort", "reason": "Too many recent actions"})
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))

    from chloe.actions.deliberate import deliberate
    action = _make_action()
    verdict = await deliberate(action)

    assert verdict.decision == "abort"
    assert "recent" in verdict.reason


@pytest.mark.asyncio
async def test_deliberate_llm_failure_treats_as_proceed(monkeypatch):
    mock_flash = AsyncMock(return_value=None)
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))

    from chloe.actions.deliberate import deliberate
    action = _make_action()
    verdict = await deliberate(action)

    assert verdict is None  # Gate treats None as proceed


@pytest.mark.asyncio
async def test_gate_aborts_on_deliberation(monkeypatch, db):
    from chloe.actions import gate

    mock_flash = AsyncMock(return_value={"decision": "abort", "reason": "Too chatty today"})
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))
    monkeypatch.setattr("chloe.actions.deliberate.should_deliberate", lambda a: True)

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock()
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = _make_action()
    result = await gate.submit(action)

    assert result.suppressed
    mock_registry.execute.assert_not_called()

    row = get_connection().execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "held_back"
```

## Acceptance criteria

- `deliberate(action)` calls the Flash LLM with a structured payload including `recent_audit` and `budget_throttle`.
- `decision="abort"` → gate suppresses action, stores `held_back` memory.
- `decision="proceed"` → gate proceeds to execution.
- `None` from LLM (failure) → treated as `proceed` (fail-open deliberation).
- Gate replaces the `should_proceed = True` stub with real deliberation call.

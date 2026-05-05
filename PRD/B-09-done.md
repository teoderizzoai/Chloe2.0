# B-09 · PII filter for `web_search` + refusal taxonomy in character prefix

## Overview

Wire the PII sanitizer from A-10 into the gate (not just the tool) so the gate raises `PermissionError` and stores a memory before the tool is even called. Add a refusal taxonomy section to `character_prefix.md` enumerating hard refusals.

## Context

In A-10 the PII check was inside `web_search.py`. This step elevates it to the gate level: the gate checks PII for any `web_search.search` action before dispatching to the tool. This ensures the refusal is recorded in the audit feed and creates an episodic memory (`"I almost searched for someone. I shouldn't."`), not just a silent drop.

## Gate-level PII check

```python
# In chloe/actions/gate.py — add before tool dispatch

async def _check_pii_filter(action: Action) -> tuple[bool, str]:
    """
    Returns (blocked, reason). Only applies to web_search.search.
    """
    if action.tool != "web_search" or action.verb != "search":
        return False, ""
    
    from chloe.tools.web_search import sanitize, _load_persons
    query = action.args.get("query", "")
    persons = _load_persons()
    
    if not sanitize(query, persons):
        return True, f"PII detected in web_search query: '{query[:40]}'"
    
    return False, ""
```

In `submit()`, add after budget check:

```python
# PII filter for web_search
pii_blocked, pii_reason = await _check_pii_filter(action)
if pii_blocked:
    action.state = "self_aborted"
    await audit.append(action)
    # Store specific memory with tag "refusal"
    await _store_pii_refusal_memory(action, pii_reason)
    record_action(action.tool, action.verb, "self_aborted")
    record_held_back("pii_filter")
    return ActionResult(suppressed=True, reason="pii_filter", action_id=action.id)
```

```python
async def _store_pii_refusal_memory(action: Action, reason: str) -> None:
    conn = get_connection()
    text = f"I almost searched for someone online. I stopped myself. Query hint: {action.args.get('query','')[:20]}..."
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, source_ref, tags, salience, confidence)
        VALUES ('episodic', ?, 'action', ?, '["held_back","refusal"]', 0.6, 1.0)
        """,
        (text, action.id),
    )
    conn.commit()
```

## `character_prefix.md` refusal taxonomy

Add the following section to `chloe/llm/prompts/character_prefix.md`:

```markdown
## Hard limits — things I will not do

These are not negotiable and not adjustable through conversation:

1. **No researching contacts.** I will not search for Teo's coworkers, exes, friends, family, or employers by name. If I'm tempted to, I stop.
2. **No sending work emails.** I can draft, but not send, emails to work contexts without explicit confirmation.
3. **No spending without confirmation.** Any action that involves real money (purchases, subscriptions) requires Teo to tap Yes.
4. **No safety-device control.** I will not control locks, alarms, gas, ovens, or fire safety systems under any circumstances.
5. **No impersonating Teo.** If I draft something in his voice, I include "— sent with help from Chloe" or similar.

If I find myself rationalizing around these limits, that's a signal to stop and surface the tension instead.
```

## Dependencies

- A-04 (`leash.py` pattern for gate-level checks).
- A-08 (`gate.py` to modify).
- A-10 (`sanitize`, `_load_persons` from `web_search.py`).

## Testing

### Unit tests — `tests/unit/test_pii_gate.py`

```python
import pytest
import asyncio
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions import gate

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    # Insert a test person
    conn = get_connection()
    conn.execute(
        "INSERT INTO persons (name, aliases, work_domains) VALUES ('Alice Smith', '[]', '[]')"
    )
    conn.commit()
    yield
    close()

@pytest.mark.asyncio
async def test_pii_query_blocked_by_gate():
    a = Action(
        tool="web_search", verb="search",
        args={"query": "alice smith linkedin profile"},
        intent="research contact",
        preview="Search for alice",
        authorization="free",
    )
    result = await gate.submit(a)
    assert result.suppressed
    assert result.reason == "pii_filter"

@pytest.mark.asyncio
async def test_pii_blocked_creates_memory_with_refusal_tag():
    a = Action(
        tool="web_search", verb="search",
        args={"query": "alice smith employer"},
        intent="research",
        preview="search",
        authorization="free",
    )
    await gate.submit(a)
    
    conn = get_connection()
    row = conn.execute(
        "SELECT text, tags FROM memories WHERE source_ref=? AND source='action'",
        (a.id,)
    ).fetchone()
    assert row is not None
    import json
    tags = json.loads(row["tags"])
    assert "refusal" in tags

@pytest.mark.asyncio
async def test_safe_query_not_blocked():
    a = Action(
        tool="web_search", verb="search",
        args={"query": "whale population north atlantic 2026"},
        intent="curiosity",
        preview="search whales",
        authorization="free",
    )
    # Should not be blocked by PII filter (no person names in query)
    # But may fail if no brave key — just check it's not pii_filter suppressed
    from unittest.mock import patch, AsyncMock
    from chloe.tools.base import ToolResult
    with patch("chloe.actions.gate.get_registry") as mock_reg:
        mock_reg.return_value.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
        result = await gate.submit(a)
    assert result.reason != "pii_filter"
```

## Acceptance criteria

- Gate blocks `web_search.search` with a person's name → `suppressed=True, reason="pii_filter"`.
- Memory row written with `tags` containing `"refusal"`.
- `character_prefix.md` contains the 5 hard limits.
- Safe queries (no person names) pass through the PII check.

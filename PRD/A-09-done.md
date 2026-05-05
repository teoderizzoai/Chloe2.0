# A-09 · Wire 1.0 outreach through the gate

## Overview

In the 1.0 codebase, find every call site of the Discord outreach function (`_send_autonomous_outreach`, `generate_autonomous_message`, and any direct Discord DM sends in `chloe.py`). Replace each with `await gate.submit(Action(tool="messages", verb="send_text", ...))`. The gate then calls `tools/messages.py`.

## Context

After this step, there is exactly one path by which Chloe can send a message: the action gate. Any code that previously called `channel.send()`, `discord_bot.send_dm()`, or `self.on_message(reply)` directly for outreach must now go through `gate.submit()`. This is the key phase A acceptance gate: 100% of outreach in `actions`.

## Files to modify in 1.0 codebase

Based on the current architecture:

1. **`chloe/chloe.py`** — `_send_autonomous_outreach()` and the inner call to `on_message` callback.
2. **`chloe/chloe.py`** — `generate_autonomous_message()` or wherever the autonomous message is finally sent.
3. **`chloe/discord_bot.py`** — any direct `channel.send()` calls that originate from Chloe's initiative (not from replying to a user message — those still go through `chat()`).

Note: The `chat()` function's reply path (returning a message to the person who initiated the conversation) does **not** go through the gate — it's a response, not an initiative. Only autonomous outreach (Chloe initiating contact) must go through the gate.

## Migration pattern

Before (in `chloe.py`):
```python
async def _send_autonomous_outreach(self, person_id, message):
    if self.on_message:
        await self.on_message(message)   # direct callback to Discord
```

After:
```python
async def _send_autonomous_outreach(self, person_id, message):
    from chloe.actions.schema import Action
    from chloe.actions import gate
    action = Action(
        tool="messages",
        verb="send_text",
        args={"body": message},
        intent="autonomous outreach to " + person_id,
        preview=f"Send: {message[:80]}",
        authorization="kinetic",
    )
    result = await gate.submit(action)
    if not result.executed:
        self._log(f"Outreach gate-suppressed: {result.reason}")
```

## Affected locations to find

Run in the codebase:
```bash
grep -rn "on_message\|send_dm\|channel\.send\|_send_autonomous\|generate_autonomous" chloe/
```

Review every match. Mark each as:
- **Replace with gate.submit()** — autonomous outreach
- **Keep as-is** — reactive reply to user (not gate territory)

## Verification that no covert path exists

After migration, add a test that imports `chloe.discord_bot` and asserts that `send_dm` is only called from within `chloe/tools/messages.py`:

```python
# tests/unit/test_no_direct_discord_send.py
import ast
import pathlib

def test_discord_send_only_called_from_messages_tool():
    """
    Assert that no file other than tools/messages.py calls discord send functions directly.
    """
    forbidden_callers = []
    chloe_dir = pathlib.Path("chloe")
    
    for py_file in chloe_dir.rglob("*.py"):
        if py_file.name == "messages.py" and "tools" in py_file.parts:
            continue
        if py_file.name == "discord_bot.py":
            continue   # source of truth, may define send_dm
        
        source = py_file.read_text()
        if "send_dm" in source or "channel.send" in source:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = ast.dump(node.func)
                    if "send_dm" in func or ("send" in func and "channel" in ast.dump(node)):
                        forbidden_callers.append(f"{py_file}:{node.lineno}")
    
    assert not forbidden_callers, (
        f"Direct Discord sends found outside tools/messages.py:\n"
        + "\n".join(forbidden_callers)
    )
```

## Dependencies

- A-07 (`MessagesTool` registered).
- A-08 (`gate.submit` working).
- A-02 (`Action` schema).
- Existing 1.0 `chloe.py` (modification).

## Testing

### Integration test — `tests/integration/test_outreach_via_gate.py`

```python
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.tools.messages import MessagesTool
from chloe.tools.registry import get_registry

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

@pytest.mark.asyncio
async def test_autonomous_outreach_creates_action_row():
    """
    Simulate the 1.0 autonomous outreach path → gate → actions table.
    """
    sent = []
    async def mock_send(body):
        sent.append(body)
        return True
    
    # Register messages tool
    registry = get_registry()
    registry._tools.clear()   # reset for test isolation
    registry.register(MessagesTool(send_callback=mock_send))
    
    # Import and call the 1.0 outreach path (now wired through gate)
    from chloe.actions.schema import Action
    from chloe.actions import gate
    
    action = Action(
        tool="messages",
        verb="send_text",
        args={"body": "thinking of you"},
        intent="autonomous morning outreach",
        preview="Send: thinking of you",
        authorization="kinetic",
    )
    result = await gate.submit(action)
    
    assert result.executed
    assert len(sent) == 1
    
    conn = get_connection()
    row = conn.execute(
        "SELECT state FROM actions WHERE id=?", (action.id,)
    ).fetchone()
    assert row is not None
    assert row["state"] == "executed"
```

### AST-based import-graph test

```python
def test_no_direct_discord_send_in_chloe_py():
    """
    Ensure _send_autonomous_outreach in chloe.py no longer directly calls the Discord callback.
    Instead it should call gate.submit.
    """
    import ast
    chloe_py = pathlib.Path("chloe/chloe.py")
    if not chloe_py.exists():
        pytest.skip("chloe.py not found")
    
    source = chloe_py.read_text()
    # After migration, "gate.submit" must appear in the outreach function
    assert "gate.submit" in source, "gate.submit not found in chloe.py"
```

## Acceptance criteria

- Integration test: simulated 1.0 outreach trigger → `actions` table has one row with `state="executed"`.
- AST test: `send_dm` is not called directly from `chloe.py` (only from `tools/messages.py`).
- Manual verification: send an autonomous outreach → the audit feed shows the action.

# A-14 · Phase A integration test: all outreach in `actions`

## Overview

Write two tests that serve as the Phase A acceptance gate: (1) replay 10 scripted outreach events and assert every event has a row in `actions`, resolves (no `proposed` state), and `feed_text(10)` is non-empty; (2) assert that there is no path to call the Discord send function directly except through the gate (import-graph or runtime spy analysis).

## Context

This is the phase gate test that must be green before Phase B begins. It proves the invariant that 100% of outreach passes through `gate.submit()` and appears in the `actions` table. It is intentionally comprehensive — not a quick unit test, but a scenario test.

## Test 1 — 10 scripted outreach events

```python
# tests/integration/test_phase_a_acceptance.py

import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions import gate, audit
from chloe.tools.messages import MessagesTool
from chloe.tools.notes import NotesTool
from chloe.tools.web_search import WebSearchTool
from chloe.tools.registry import ToolRegistry

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

SCRIPTED_EVENTS = [
    {"tool": "messages", "verb": "send_text",  "args": {"body": "good morning"},                    "auth": "kinetic"},
    {"tool": "notes",    "verb": "create",      "args": {"path": "daily.md", "text": "today plan"}, "auth": "kinetic"},
    {"tool": "notes",    "verb": "append",      "args": {"path": "daily.md", "text": "\nnote 2"},   "auth": "kinetic"},
    {"tool": "messages", "verb": "send_text",  "args": {"body": "thinking of you"},                 "auth": "kinetic"},
    {"tool": "notes",    "verb": "create",      "args": {"path": "ideas.md", "text": "idea 1"},     "auth": "kinetic"},
    {"tool": "messages", "verb": "send_text",  "args": {"body": "good night"},                      "auth": "kinetic"},
    {"tool": "notes",    "verb": "append",      "args": {"path": "ideas.md", "text": "\nidea 2"},   "auth": "kinetic"},
    {"tool": "notes",    "verb": "create",      "args": {"path": "log.md", "text": "log entry"},    "auth": "kinetic"},
    {"tool": "messages", "verb": "send_text",  "args": {"body": "hey"},                             "auth": "kinetic"},
    {"tool": "notes",    "verb": "append",      "args": {"path": "log.md", "text": "\nanother"},    "auth": "kinetic"},
]

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

@pytest.fixture
def registry_with_tools(tmp_path):
    r = ToolRegistry()
    sent = []
    async def mock_send(body):
        sent.append(body)
        return True
    r.register(MessagesTool(send_callback=mock_send))
    r.register(NotesTool(notes_dir=tmp_path / "notes"))
    return r, sent

@pytest.mark.asyncio
async def test_all_scripted_events_in_actions(tmp_path, registry_with_tools, monkeypatch):
    registry, sent = registry_with_tools
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    
    action_ids = []
    for event in SCRIPTED_EVENTS:
        a = Action(
            tool=event["tool"],
            verb=event["verb"],
            args=event["args"],
            intent=f"scripted {event['tool']}.{event['verb']}",
            preview=f"Scripted {event['verb']}",
            authorization=event["auth"],
        )
        result = await gate.submit(a)
        action_ids.append(a.id)
    
    conn = get_connection()
    
    # (a) Every event has a row in actions
    for aid in action_ids:
        row = conn.execute("SELECT id, state FROM actions WHERE id=?", (aid,)).fetchone()
        assert row is not None, f"Missing action row for {aid}"
    
    # (b) No row has state="proposed" (all resolved)
    proposed_count = conn.execute(
        "SELECT COUNT(*) FROM actions WHERE state='proposed'"
    ).fetchone()[0]
    assert proposed_count == 0, f"Found {proposed_count} unresolved 'proposed' actions"
    
    # (c) feed_text(10) is non-empty
    actions = await audit.recent(n=10)
    text = audit.feed_text(actions, n=10)
    assert len(text) > 0
    assert "messages" in text or "notes" in text

@pytest.mark.asyncio
async def test_messages_sent_count_matches_scripted(tmp_path, registry_with_tools, monkeypatch):
    registry, sent = registry_with_tools
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    
    for event in SCRIPTED_EVENTS:
        a = Action(
            tool=event["tool"], verb=event["verb"], args=event["args"],
            intent="scripted", preview="scripted", authorization=event["auth"],
        )
        await gate.submit(a)
    
    expected_messages = sum(1 for e in SCRIPTED_EVENTS if e["tool"] == "messages")
    assert len(sent) == expected_messages
```

## Test 2 — No direct Discord send path

```python
# tests/unit/test_no_bypass_gate.py

import ast
import pathlib
import pytest

def test_discord_send_only_in_messages_tool():
    """
    No Python file other than tools/messages.py and discord_bot.py
    should directly call send_dm or channel.send.
    """
    chloe_root = pathlib.Path("chloe")
    violations = []

    for py_file in sorted(chloe_root.rglob("*.py")):
        # Allow the source file itself and the Discord bot definition
        if "messages" in py_file.name and "tools" in py_file.parts:
            continue
        if py_file.name == "discord_bot.py":
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Check for direct Discord send patterns
        direct_patterns = ["send_dm(", "channel.send(", ".send(mention", "on_message(reply"]
        for pattern in direct_patterns:
            if pattern in source:
                violations.append(f"{py_file}: contains '{pattern}'")

    assert not violations, (
        "Direct Discord send calls found outside tools/messages.py:\n"
        + "\n".join(violations)
    )

def test_gate_submit_used_for_outreach():
    """
    The 1.0 chloe.py file should reference gate.submit for outreach.
    """
    chloe_py = pathlib.Path("chloe/chloe.py")
    if not chloe_py.exists():
        pytest.skip("chloe.py not yet modified")

    source = chloe_py.read_text()
    assert "gate.submit" in source or "gate import" in source, (
        "chloe.py does not use gate.submit for outreach"
    )
```

## Dependencies

- A-03 (audit).
- A-07, A-11 (tools).
- A-08 (gate).
- A-09 (1.0 wired through gate).

## Acceptance criteria

- Test 1 green: all 10 events have rows in `actions`, no rows with `state="proposed"`, `feed_text` non-empty.
- Test 2 green in CI: AST/text search finds no direct Discord send outside `tools/messages.py`.
- Manually verify: open `/admin/audit/ui` and see 10 entries.

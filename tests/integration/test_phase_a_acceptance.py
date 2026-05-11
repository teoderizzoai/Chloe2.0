import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from chloe.actions import audit, gate
from chloe.actions.schema import Action
from chloe.state.db import close, get_connection, migrate
from chloe.tools.messages import MessagesTool
from chloe.tools.notes import NotesTool
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
def no_leash():
    with patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")):
        with patch("chloe.actions.deliberate.should_deliberate", return_value=False):
            yield


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
        await gate.submit(a)
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

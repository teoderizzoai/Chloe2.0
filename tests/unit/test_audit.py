import pytest
from pathlib import Path
from datetime import datetime, timezone
from chloe.state.db import migrate, close
from chloe.actions.schema import Action
from chloe.actions import audit

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def _make_action(tool="spotify", verb="queue_track", state="executed", intent="test intent"):
    return Action(
        tool=tool, verb=verb,
        args={"uri": "spotify:track:test"},
        intent=intent,
        preview=f"Would {verb}",
        authorization="kinetic",
        state=state,
    )


@pytest.mark.asyncio
async def test_append_and_recent():
    a1 = _make_action(intent="first")
    a2 = _make_action(intent="second")
    a3 = _make_action(intent="third")
    await audit.append(a1)
    await audit.append(a2)
    await audit.append(a3)

    results = await audit.recent(n=10)
    assert len(results) == 3
    assert results[0].intent == "third"


@pytest.mark.asyncio
async def test_append_upserts_on_conflict():
    a = _make_action(intent="original")
    await audit.append(a)
    a.state = "executed"
    await audit.append(a)
    results = await audit.recent(n=10)
    assert len(results) == 1
    assert results[0].state == "executed"


@pytest.mark.asyncio
async def test_feed_text_contains_tool_verb_state():
    a = _make_action(tool="spotify", verb="queue_track", state="executed", intent="calm song")
    await audit.append(a)
    actions = await audit.recent(n=10)
    text = audit.feed_text(actions, n=3)
    assert "spotify" in text
    assert "queue_track" in text
    assert "calm song" in text


@pytest.mark.asyncio
async def test_feed_text_respects_n_limit():
    for i in range(5):
        await audit.append(_make_action(intent=f"intent {i}"))
    actions = await audit.recent(n=200)
    text = audit.feed_text(actions, n=3)
    assert text.count("\n") == 2


def test_feed_text_empty_list():
    text = audit.feed_text([], n=10)
    assert "no recent" in text.lower()

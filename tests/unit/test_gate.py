import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close
from chloe.actions.schema import Action, ActionResult
from chloe.actions import gate
from chloe.tools.base import ToolResult

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def make_action(auth="kinetic", tool="messages", verb="send_text"):
    return Action(
        tool=tool, verb=verb,
        intent="test intent",
        preview="test preview",
        authorization=auth,
        args={"body": "hello"},
    )


@pytest.mark.asyncio
async def test_free_action_passes_through():
    a = make_action(auth="free", tool="notes", verb="read")
    mock_execute = AsyncMock(return_value=ToolResult(success=True, data={}))
    with patch("chloe.actions.gate.get_registry") as mock_reg:
        mock_reg.return_value.execute = mock_execute
        result = await gate.submit(a)
    assert result.executed
    assert not result.suppressed


@pytest.mark.asyncio
async def test_leash_blocked_action_is_suppressed():
    a = make_action(auth="kinetic")
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()

    result = await gate.submit(a)
    assert result.suppressed
    assert not result.executed


@pytest.mark.asyncio
async def test_suppressed_action_in_db():
    a = make_action(auth="kinetic")
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()

    await gate.submit(a)

    row = conn.execute(
        "SELECT state FROM actions WHERE id=?", (a.id,)
    ).fetchone()
    assert row is not None
    assert row["state"] == "suppressed_by_leash"


@pytest.mark.asyncio
async def test_kinetic_sensitive_awaits_confirmation():
    a = make_action(auth="kinetic-sensitive")
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("kinetic-sensitive"),),
    )
    conn.commit()
    import unittest.mock as m
    with m.patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")), \
         m.patch("chloe.channels.push.get_teo_device_info", return_value={}):
        result = await gate.submit(a)
    assert result.awaiting
    assert result.ticket_id
    assert not result.executed


@pytest.mark.asyncio
async def test_held_back_memory_written():
    a = make_action(auth="kinetic")
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()

    await gate.submit(a)

    row = conn.execute(
        "SELECT text, tags FROM memories WHERE source='action' AND source_ref=?",
        (a.id,)
    ).fetchone()
    assert row is not None
    assert "held_back" in row["tags"]


@pytest.mark.asyncio
async def test_budget_exceeded_self_aborts():
    a = make_action(auth="kinetic")

    with patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")), \
         patch("chloe.actions.gate.budget.exceeded_for", return_value=True):
        result = await gate.submit(a)

    assert result.suppressed
    assert result.reason == "budget_exceeded"

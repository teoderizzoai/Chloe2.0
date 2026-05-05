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
    """Autonomous outreach path → gate → actions table has one row with state=executed."""
    sent = []

    async def mock_send(body):
        sent.append(body)
        return True

    registry = get_registry()
    registry._tools.clear()
    registry.register(MessagesTool(send_callback=mock_send))

    from chloe.chloe import ChloeCore
    core = ChloeCore()
    with patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")):
        await core._send_autonomous_outreach("teo", "thinking of you")

    assert len(sent) == 1
    assert sent[0] == "thinking of you"

    conn = get_connection()
    row = conn.execute(
        "SELECT state FROM actions WHERE tool='messages' AND verb='send_text'"
    ).fetchone()
    assert row is not None
    assert row["state"] == "executed"


@pytest.mark.asyncio
async def test_gate_suppressed_outreach_not_sent():
    """When the gate suppresses an action, nothing is sent."""
    sent = []

    async def mock_send(body):
        sent.append(body)
        return True

    import json
    registry = get_registry()
    registry._tools.clear()
    registry.register(MessagesTool(send_callback=mock_send))

    conn = get_connection()
    conn.execute(
        "UPDATE preferences SET value=? WHERE key='auth_ceiling'",
        (json.dumps("intimate"),),
    )
    conn.commit()

    from chloe.chloe import ChloeCore
    core = ChloeCore()
    await core._send_autonomous_outreach("teo", "thinking of you")

    assert len(sent) == 0

    row = conn.execute(
        "SELECT state FROM actions WHERE tool='messages' AND verb='send_text'"
    ).fetchone()
    assert row is not None
    assert row["state"] == "suppressed_by_leash"

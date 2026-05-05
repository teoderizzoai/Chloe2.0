import json
from pathlib import Path

import pytest

from chloe.actions import gate
from chloe.actions.schema import Action
from chloe.state.db import close, get_connection, migrate

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
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
        (a.id,),
    ).fetchone()
    assert row is not None
    tags = json.loads(row["tags"])
    assert "refusal" in tags


@pytest.mark.asyncio
async def test_safe_query_not_blocked():
    from unittest.mock import AsyncMock, patch

    from chloe.tools.base import ToolResult

    a = Action(
        tool="web_search", verb="search",
        args={"query": "whale population north atlantic 2026"},
        intent="curiosity",
        preview="search whales",
        authorization="free",
    )
    with patch("chloe.actions.gate.get_registry") as mock_reg:
        mock_reg.return_value.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
        result = await gate.submit(a)
    assert result.reason != "pii_filter"


@pytest.mark.asyncio
async def test_pii_action_row_state_is_self_aborted():
    a = Action(
        tool="web_search", verb="search",
        args={"query": "alice smith home address"},
        intent="research",
        preview="search",
        authorization="free",
    )
    await gate.submit(a)

    conn = get_connection()
    row = conn.execute(
        "SELECT state FROM actions WHERE id=?", (a.id,)
    ).fetchone()
    assert row["state"] == "self_aborted"

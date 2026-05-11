import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.actions import gate
from chloe.tools.base import ToolResult

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    close()
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture(autouse=True)
def no_leash():
    with patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")):
        yield


@pytest.mark.asyncio
async def test_successful_action_creates_memory(db, monkeypatch):
    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=True,
        data={"queued": "spotify:track:xyz"},
        artifact_ref="spotify:track:xyz",
        artifact_kind="spotify_track",
    ))
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = Action(
        tool="spotify", verb="queue_track",
        args={"uri": "spotify:track:xyz"},
        intent="Queue a favourite track to set the mood",
        preview="Queue Radiohead track",
        authorization="kinetic",
    )
    result = await gate.submit(action)

    assert result.executed

    row = db.execute("SELECT * FROM memories WHERE source='action'").fetchone()
    assert row is not None
    assert "Queue a favourite track to set the mood" in row["text"]
    assert row["source_ref"] == action.id

    artifact_refs = json.loads(row["artifact_refs"])
    assert len(artifact_refs) == 1
    assert artifact_refs[0]["ref"] == "spotify:track:xyz"

    action_row = db.execute("SELECT becomes_memory_id FROM actions WHERE id=?", (action.id,)).fetchone()
    assert action_row["becomes_memory_id"] == row["id"]


@pytest.mark.asyncio
async def test_failed_action_does_not_create_memory(db, monkeypatch):
    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=False,
        error="Spotify token expired",
    ))
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = Action(
        tool="spotify", verb="queue_track",
        args={"uri": "spotify:track:xyz"},
        intent="Queue a track",
        preview="Queue track",
        authorization="kinetic",
    )
    await gate.submit(action)

    count = db.execute("SELECT COUNT(*) FROM memories WHERE source='action'").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_memory_has_action_tag(db, monkeypatch):
    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = Action(
        tool="notes", verb="append",
        args={"path": "journal.md", "text": "Today was good"},
        intent="Append a journal entry",
        preview="Append to journal",
        authorization="kinetic",
    )
    await gate.submit(action)

    row = db.execute("SELECT tags FROM memories WHERE source='action'").fetchone()
    tags = json.loads(row["tags"])
    assert "action" in tags

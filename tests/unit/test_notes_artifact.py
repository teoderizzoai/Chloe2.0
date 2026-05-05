import pytest
from pathlib import Path

from chloe.state.db import migrate, close, get_connection
from chloe.tools.notes import NotesTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture
def setup(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    tool = NotesTool(notes_dir=tmp_path / "notes")
    yield tool
    close()


@pytest.mark.asyncio
async def test_create_registers_artifact(setup):
    tool = setup
    await tool.execute("create", {"path": "plan.md", "text": "my plan"})
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='plan.md'").fetchone()
    assert row is not None
    assert row["kind"] == "notes_doc"
    assert "my plan" in row["snapshot"]


@pytest.mark.asyncio
async def test_append_updates_artifact(setup):
    tool = setup
    await tool.execute("create", {"path": "doc.md", "text": "original"})
    await tool.execute("append", {"path": "doc.md", "text": "\naddition"})
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='doc.md'").fetchone()
    assert row is not None
    assert "original" in row["snapshot"]


@pytest.mark.asyncio
async def test_read_does_not_create_artifact(setup):
    tool = setup
    await tool.execute("create", {"path": "r.md", "text": "hi"})
    conn = get_connection()
    conn.execute("DELETE FROM artifact_index")
    conn.commit()
    await tool.execute("read", {"path": "r.md"})
    count = conn.execute("SELECT COUNT(*) FROM artifact_index").fetchone()[0]
    assert count == 0

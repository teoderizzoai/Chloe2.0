import pytest
from pathlib import Path
from chloe.tools.notes import NotesTool


@pytest.fixture
def tool(tmp_path):
    return NotesTool(notes_dir=tmp_path / "notes")


@pytest.mark.asyncio
async def test_create_file(tool):
    result = await tool.execute("create", {"path": "test.md", "text": "hello"})
    assert result.success
    assert (tool._root / "test.md").read_text() == "hello"


@pytest.mark.asyncio
async def test_read_file(tool):
    await tool.execute("create", {"path": "r.md", "text": "content"})
    result = await tool.execute("read", {"path": "r.md"})
    assert result.success
    assert result.data["text"] == "content"


@pytest.mark.asyncio
async def test_append_and_revert(tool):
    await tool.execute("create", {"path": "a.md", "text": "original"})
    await tool.execute("append", {"path": "a.md", "text": "\nappended"})

    text_after_append = (tool._root / "a.md").read_text()
    assert "appended" in text_after_append

    await tool.execute("revert", {"path": "a.md"})
    text_after_revert = (tool._root / "a.md").read_text()
    assert text_after_revert == "original"


@pytest.mark.asyncio
async def test_path_traversal_rejected(tool):
    result = await tool.execute("read", {"path": "../../../etc/passwd"})
    assert not result.success
    assert "traversal" in result.error.lower() or "permission" in result.error.lower()


@pytest.mark.asyncio
async def test_list_directory(tool):
    await tool.execute("create", {"path": "a.md", "text": "a"})
    await tool.execute("create", {"path": "b.md", "text": "b"})
    result = await tool.execute("list", {"dir": ""})
    assert result.success
    assert "a.md" in result.data["files"]
    assert "b.md" in result.data["files"]


@pytest.mark.asyncio
async def test_create_returns_artifact_ref(tool):
    result = await tool.execute("create", {"path": "note.md", "text": "hi"})
    assert result.artifact_ref == "note.md"
    assert result.artifact_kind == "notes_doc"


@pytest.mark.asyncio
async def test_create_duplicate_fails(tool):
    await tool.execute("create", {"path": "dup.md", "text": "first"})
    result = await tool.execute("create", {"path": "dup.md", "text": "second"})
    assert not result.success
    assert "Already exists" in result.error


@pytest.mark.asyncio
async def test_read_missing_file(tool):
    result = await tool.execute("read", {"path": "ghost.md"})
    assert not result.success
    assert "Not found" in result.error


@pytest.mark.asyncio
async def test_move(tool):
    await tool.execute("create", {"path": "src.md", "text": "data"})
    result = await tool.execute("move", {"src": "src.md", "dst": "dst.md"})
    assert result.success
    assert not (tool._root / "src.md").exists()
    assert (tool._root / "dst.md").read_text() == "data"


@pytest.mark.asyncio
async def test_revert_no_versions(tool):
    await tool.execute("create", {"path": "fresh.md", "text": "new"})
    result = await tool.execute("revert", {"path": "fresh.md"})
    assert not result.success
    assert "No versions" in result.error


@pytest.mark.asyncio
async def test_unknown_verb(tool):
    result = await tool.execute("explode", {})
    assert not result.success
    assert "Unknown verb" in result.error

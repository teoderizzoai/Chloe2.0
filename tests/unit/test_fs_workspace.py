import pytest
from pathlib import Path
from chloe.tools.fs_workspace import FsWorkspaceTool, MAX_FILE_SIZE


@pytest.fixture
def tool(tmp_path):
    return FsWorkspaceTool(workspace_dir=tmp_path / "workspace")


@pytest.mark.asyncio
async def test_write_and_read(tool):
    result = await tool.execute("write", {"path": "draft.md", "text": "draft content"})
    assert result.success
    result2 = await tool.execute("read", {"path": "draft.md"})
    assert result2.data["text"] == "draft content"


@pytest.mark.asyncio
async def test_write_exceeds_per_file_limit(tool):
    big_text = "x" * (MAX_FILE_SIZE + 1)
    result = await tool.execute("write", {"path": "big.txt", "text": big_text})
    assert not result.success
    assert "10 MB" in result.error or "CapExceeded" in result.error


@pytest.mark.asyncio
async def test_delete_removes_file(tool):
    await tool.execute("write", {"path": "to_delete.md", "text": "bye"})
    result = await tool.execute("delete", {"path": "to_delete.md"})
    assert result.success
    result2 = await tool.execute("read", {"path": "to_delete.md"})
    assert not result2.success


@pytest.mark.asyncio
async def test_path_traversal_rejected(tool):
    result = await tool.execute("read", {"path": "../../etc/passwd"})
    assert not result.success


@pytest.mark.asyncio
async def test_list_returns_files(tool):
    await tool.execute("write", {"path": "a.txt", "text": "a"})
    await tool.execute("write", {"path": "b.txt", "text": "b"})
    result = await tool.execute("list", {"dir": ""})
    assert result.success
    assert "a.txt" in result.data["files"]


@pytest.mark.asyncio
async def test_all_verbs_are_free_auth(tool):
    for verb, vobj in tool.verbs.items():
        assert vobj.auth_class == "free", f"{verb} should be free"


@pytest.mark.asyncio
async def test_read_missing_file(tool):
    result = await tool.execute("read", {"path": "nonexistent.md"})
    assert not result.success
    assert "Not found" in result.error


@pytest.mark.asyncio
async def test_delete_missing_file(tool):
    result = await tool.execute("delete", {"path": "ghost.md"})
    assert not result.success


@pytest.mark.asyncio
async def test_write_returns_bytes(tool):
    result = await tool.execute("write", {"path": "f.txt", "text": "hello"})
    assert result.success
    assert result.data["bytes"] == 5


@pytest.mark.asyncio
async def test_unknown_verb(tool):
    result = await tool.execute("revert", {"path": "f.txt"})
    assert not result.success

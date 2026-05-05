# A-12 · `tools/fs_workspace.py` — Chloe's own workspace

## Overview

Implement `chloe/tools/fs_workspace.py`. Same verbs as `notes` (`read`, `write`, `list`, `delete`) but rooted at `CHLOE_WORKSPACE_DIR`. Auth: `free` (her own files). 10 MB per-file cap, 1 GB total cap enforced at write time. No `.versions/` shadow (she owns these files and doesn't need user-facing revert).

## Context

The workspace is Chloe's private scratch space — drafts, plans, partial work, things she's thinking about. Unlike `notes`, the user doesn't read this directly (though the "What Chloe's doing" tab surfaces a `kv["current_activity"]` line that may reference workspace content). The auth class is `free` because this is her own directory: no human account, no vendor API, no confirmation needed.

## Key differences from `notes`

| Feature | notes | fs_workspace |
|---|---|---|
| Root | `CHLOE_NOTES_DIR` | `CHLOE_WORKSPACE_DIR` |
| Auth | intimate (read) / kinetic (write) | free (all) |
| Versioning | `.versions/` shadow | No |
| Per-file cap | None | 10 MB |
| Total cap | None | 1 GB |
| Verb: `revert` | Yes | No |
| Verb: `delete` | No | Yes |

## Implementation

```python
# chloe/tools/fs_workspace.py

from chloe.tools.base import Tool, ToolVerb, ToolResult, CapExceededError
from chloe.config import get_settings
from pathlib import Path

MAX_FILE_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_TOTAL_SIZE = 1 * 1024 * 1024 * 1024  # 1 GB

class FsWorkspaceTool(Tool):
    name = "fs_workspace"

    def __init__(self, workspace_dir: Path | None = None):
        s = get_settings()
        self._root = workspace_dir or s.chloe_workspace_dir
        self.verbs = {
            "read": ToolVerb(
                name="read",
                schema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},
                auth_class="free", reversibility=1.0,
                description_for_model="Read a file from your personal workspace.",
                description_for_human="Read workspace file",
            ),
            "write": ToolVerb(
                name="write",
                schema={"type":"object","properties":{"path":{"type":"string"},"text":{"type":"string"}},"required":["path","text"]},
                auth_class="free", reversibility=0.5,
                description_for_model="Write or overwrite a file in your personal workspace. "
                    "10 MB per file, 1 GB total limit.",
                description_for_human="Write workspace file",
            ),
            "list": ToolVerb(
                name="list",
                schema={"type":"object","properties":{"dir":{"type":"string"}}},
                auth_class="free", reversibility=1.0,
                description_for_model="List files in your workspace.",
                description_for_human="List workspace files",
            ),
            "delete": ToolVerb(
                name="delete",
                schema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},
                auth_class="free", reversibility=0.0,
                description_for_model="Delete a file from your workspace.",
                description_for_human="Delete workspace file",
            ),
        }

    def _safe_path(self, rel: str) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        resolved = (self._root / rel).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise PermissionError(f"Path traversal rejected: {rel!r}")
        return resolved

    def _total_size(self) -> int:
        total = 0
        for f in self._root.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total

    async def execute(self, verb: str, args: dict) -> ToolResult:
        try:
            if verb == "read":
                p = self._safe_path(args["path"])
                if not p.exists():
                    return ToolResult(success=False, error=f"Not found: {args['path']}")
                return ToolResult(success=True, data={"text": p.read_text(encoding="utf-8")})

            elif verb == "write":
                p = self._safe_path(args["path"])
                text = args["text"]
                
                # Per-file size check
                if len(text.encode("utf-8")) > MAX_FILE_SIZE:
                    raise CapExceededError(f"File exceeds 10 MB limit")
                
                # Total workspace size check
                existing_size = p.stat().st_size if p.exists() else 0
                new_bytes = len(text.encode("utf-8"))
                if self._total_size() - existing_size + new_bytes > MAX_TOTAL_SIZE:
                    raise CapExceededError("Workspace total size would exceed 1 GB")
                
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
                return ToolResult(success=True, data={"path": args["path"], "bytes": new_bytes})

            elif verb == "list":
                d = self._safe_path(args.get("dir", ""))
                if not d.is_dir():
                    return ToolResult(success=False, error="Not a directory")
                files = [
                    str(f.relative_to(self._root))
                    for f in d.iterdir()
                    if not f.name.startswith(".")
                ]
                return ToolResult(success=True, data={"files": files})

            elif verb == "delete":
                p = self._safe_path(args["path"])
                if not p.exists():
                    return ToolResult(success=False, error=f"Not found: {args['path']}")
                p.unlink()
                return ToolResult(success=True, data={"deleted": args["path"]})

            return ToolResult(success=False, error=f"Unknown verb: {verb}")

        except CapExceededError as e:
            return ToolResult(success=False, error=str(e))
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

## Dependencies

- A-06 (Tool base classes).
- F-02 (config for `chloe_workspace_dir`).

## Testing

### Unit tests — `tests/unit/test_fs_workspace.py`

```python
import pytest
import asyncio
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
```

## Acceptance criteria

- Write 10 MB + 1 byte → `CapExceededError` (returned as error result, not exception).
- Path traversal rejected.
- All verbs have `auth_class="free"`.
- `delete` actually removes the file.

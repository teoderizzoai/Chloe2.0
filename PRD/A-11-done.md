# A-11 · `tools/notes.py` — local sandboxed directory

## Overview

Implement `chloe/tools/notes.py` with verbs `read(path)`, `append(path, text)`, `create(path, text)`, `list(dir?)`, `move(src, dst)`, `revert(path, version)`. All operations confined to `CHLOE_NOTES_DIR` (path traversal rejected). `revert` keeps a `.versions/` shadow alongside each file.

## Context

Notes is the lowest-risk write tool — it writes to a local directory that Teo owns but Chloe can also use. It's how Chloe externalises her thoughts and creates notes for Teo ("wrote a reminder for Tuesday"). In Phase A it uses a local sandboxed directory; vendor backends (Apple Notes, Google Keep) are optional in Phase G.

## Implementation

```python
# chloe/tools/notes.py

from chloe.tools.base import Tool, ToolVerb, ToolResult, CapExceededError
from chloe.config import get_settings
from pathlib import Path
import shutil
import datetime
import json

class NotesTool(Tool):
    name = "notes"

    def __init__(self, notes_dir: Path | None = None):
        s = get_settings()
        self._root = notes_dir or s.chloe_notes_dir
        self.verbs = {
            "read": ToolVerb(
                name="read", schema={"type":"object","properties":{"path":{"type":"string"}},"required":["path"]},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Read a note from the local notes directory.",
                description_for_human="Read note",
            ),
            "append": ToolVerb(
                name="append",
                schema={"type":"object","properties":{"path":{"type":"string"},"text":{"type":"string"}},"required":["path","text"]},
                auth_class="kinetic", reversibility=0.8,
                description_for_model="Append text to an existing note.",
                description_for_human="Append to note",
                reverse_verb="revert",
            ),
            "create": ToolVerb(
                name="create",
                schema={"type":"object","properties":{"path":{"type":"string"},"text":{"type":"string"}},"required":["path","text"]},
                auth_class="kinetic", reversibility=0.9,
                description_for_model="Create a new note file.",
                description_for_human="Create note",
            ),
            "list": ToolVerb(
                name="list",
                schema={"type":"object","properties":{"dir":{"type":"string"}}},
                auth_class="free", reversibility=1.0,
                description_for_model="List notes in a directory.",
                description_for_human="List notes",
            ),
            "move": ToolVerb(
                name="move",
                schema={"type":"object","properties":{"src":{"type":"string"},"dst":{"type":"string"}},"required":["src","dst"]},
                auth_class="kinetic", reversibility=0.7,
                description_for_model="Move a note to a new path.",
                description_for_human="Move note",
            ),
            "revert": ToolVerb(
                name="revert",
                schema={"type":"object","properties":{"path":{"type":"string"},"version":{"type":"integer"}},"required":["path"]},
                auth_class="kinetic", reversibility=0.9,
                description_for_model="Revert a note to a previous version.",
                description_for_human="Revert note",
            ),
        }

    def _safe_path(self, rel: str) -> Path:
        """Resolve path within root, raising PermissionError on traversal."""
        self._root.mkdir(parents=True, exist_ok=True)
        resolved = (self._root / rel).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise PermissionError(f"Path traversal rejected: {rel!r}")
        return resolved

    def _versions_path(self, path: Path) -> Path:
        return path.parent / ".versions" / path.name

    def _save_version(self, path: Path) -> None:
        if not path.exists():
            return
        vdir = self._versions_path(path)
        vdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        version_file = vdir / f"{ts}.bak"
        shutil.copy2(path, version_file)

    async def execute(self, verb: str, args: dict) -> ToolResult:
        try:
            if verb == "read":
                p = self._safe_path(args["path"])
                if not p.exists():
                    return ToolResult(success=False, error=f"Not found: {args['path']}")
                return ToolResult(success=True, data={"text": p.read_text(encoding="utf-8"), "path": args["path"]})

            elif verb == "append":
                p = self._safe_path(args["path"])
                self._save_version(p)
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding="utf-8") as f:
                    f.write(args["text"])
                return ToolResult(success=True, data={"path": args["path"]}, artifact_ref=args["path"], artifact_kind="notes_doc")

            elif verb == "create":
                p = self._safe_path(args["path"])
                if p.exists():
                    return ToolResult(success=False, error=f"Already exists: {args['path']}")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args["text"], encoding="utf-8")
                return ToolResult(success=True, data={"path": args["path"]}, artifact_ref=args["path"], artifact_kind="notes_doc")

            elif verb == "list":
                d = self._safe_path(args.get("dir", ""))
                if not d.is_dir():
                    return ToolResult(success=False, error="Not a directory")
                files = [str(f.relative_to(self._root)) for f in d.iterdir() if not f.name.startswith(".")]
                return ToolResult(success=True, data={"files": files})

            elif verb == "move":
                src = self._safe_path(args["src"])
                dst = self._safe_path(args["dst"])
                if not src.exists():
                    return ToolResult(success=False, error=f"Source not found: {args['src']}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                return ToolResult(success=True, data={"src": args["src"], "dst": args["dst"]})

            elif verb == "revert":
                p = self._safe_path(args["path"])
                vdir = self._versions_path(p)
                if not vdir.exists():
                    return ToolResult(success=False, error="No versions available")
                versions = sorted(vdir.iterdir())
                if not versions:
                    return ToolResult(success=False, error="No versions available")
                version_idx = args.get("version", -1)
                try:
                    target_version = versions[version_idx]
                except IndexError:
                    return ToolResult(success=False, error="Version index out of range")
                self._save_version(p)  # save current as new version
                shutil.copy2(target_version, p)
                return ToolResult(success=True, data={"path": args["path"], "reverted_to": target_version.name})

            return ToolResult(success=False, error=f"Unknown verb: {verb}")

        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

## Dependencies

- A-06 (Tool base classes).
- F-02 (config for `chloe_notes_dir`).

## Testing

### Unit tests — `tests/unit/test_notes_tool.py`

```python
import pytest
import asyncio
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
```

## Acceptance criteria

- `create` → `append` → `revert` leaves the file at the pre-append content.
- Path traversal (`../../../etc/passwd`) is rejected with an error.
- `list` returns files in the directory.
- `create` and `append` return `artifact_ref` for the artifact index.

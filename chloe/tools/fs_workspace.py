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

                if len(text.encode("utf-8")) > MAX_FILE_SIZE:
                    raise CapExceededError("File exceeds 10 MB limit")

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

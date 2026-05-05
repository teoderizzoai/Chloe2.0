from chloe.tools.base import Tool, ToolVerb, ToolResult, CapExceededError
from chloe.config import get_settings
from pathlib import Path
import shutil
import datetime


class NotesTool(Tool):
    name = "notes"

    def __init__(self, notes_dir: Path | None = None):
        s = get_settings()
        self._root = notes_dir or s.chloe_notes_dir
        self.verbs = {
            "read": ToolVerb(
                name="read", schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Read a note from the local notes directory.",
                description_for_human="Read note",
            ),
            "append": ToolVerb(
                name="append",
                schema={"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}}, "required": ["path", "text"]},
                auth_class="kinetic", reversibility=0.8,
                description_for_model="Append text to an existing note.",
                description_for_human="Append to note",
                reverse_verb="revert",
            ),
            "create": ToolVerb(
                name="create",
                schema={"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}}, "required": ["path", "text"]},
                auth_class="kinetic", reversibility=0.9,
                description_for_model="Create a new note file.",
                description_for_human="Create note",
            ),
            "list": ToolVerb(
                name="list",
                schema={"type": "object", "properties": {"dir": {"type": "string"}}},
                auth_class="free", reversibility=1.0,
                description_for_model="List notes in a directory.",
                description_for_human="List notes",
            ),
            "move": ToolVerb(
                name="move",
                schema={"type": "object", "properties": {"src": {"type": "string"}, "dst": {"type": "string"}}, "required": ["src", "dst"]},
                auth_class="kinetic", reversibility=0.7,
                description_for_model="Move a note to a new path.",
                description_for_human="Move note",
            ),
            "revert": ToolVerb(
                name="revert",
                schema={"type": "object", "properties": {"path": {"type": "string"}, "version": {"type": "integer"}}, "required": ["path"]},
                auth_class="kinetic", reversibility=0.9,
                description_for_model="Revert a note to a previous version.",
                description_for_human="Revert note",
            ),
        }

    def _safe_path(self, rel: str) -> Path:
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
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        shutil.copy2(path, vdir / f"{ts}.bak")

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
                self._save_version(p)
                shutil.copy2(target_version, p)
                return ToolResult(success=True, data={"path": args["path"], "reverted_to": target_version.name})

            return ToolResult(success=False, error=f"Unknown verb: {verb}")

        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=str(e))

import asyncio
import json
from typing import Any

from chloe.tools.base import Tool, ToolVerb, ToolResult, FeatureDisabledError
from chloe.config import get_settings, FEATURE_FLAGS
from chloe.observability.logging import get_logger

log = get_logger("tool_registry")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # {(tool, verb): row_dict} — loaded from dynamic_verbs table
        self._dynamic: dict[tuple[str, str], dict] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        log.info("tool_registered", name=tool.name, verbs=list(tool.verbs.keys()))

    def load_dynamic_verbs(self) -> int:
        """Read dynamic_verbs table and cache them. Returns count loaded."""
        try:
            from chloe.state.db import get_connection
            conn = get_connection()
            rows = conn.execute("SELECT * FROM dynamic_verbs").fetchall()
        except Exception as exc:
            log.warning("dynamic_verbs_load_failed", error=str(exc))
            return 0
        self._dynamic = {}
        for row in rows:
            key = (row["tool"], row["verb"])
            self._dynamic[key] = dict(row)
        log.info("dynamic_verbs_loaded", count=len(self._dynamic))
        return len(self._dynamic)

    def gemini_tool_declarations(self) -> list[dict]:
        declarations = []
        for name, tool in self._tools.items():
            if not FEATURE_FLAGS.get(name, True):
                continue
            for verb_name, verb in tool.verbs.items():
                declarations.append({
                    "name": f"{name}__{verb_name}",
                    "description": verb.description_for_model,
                    "parameters": verb.schema,
                })
        for (tool_name, verb_name), row in self._dynamic.items():
            declarations.append({
                "name": f"{tool_name}__{verb_name}",
                "description": row["description"],
                "parameters": json.loads(row["schema"]),
            })
        return [{"function_declarations": declarations}]

    def describe_static(self) -> str:
        lines = ["# Available tools\n"]
        for name, tool in self._tools.items():
            if not FEATURE_FLAGS.get(name, True):
                continue
            lines.append(f"## {name}")
            for verb_name, verb in tool.verbs.items():
                auth = verb.auth_class
                rev = " (reversible)" if verb.reversibility > 0.7 else ""
                lines.append(f"- `{verb_name}` [{auth}]{rev}: {verb.description_for_human}")
            lines.append("")
        return "\n".join(lines)

    async def execute(self, tool_name: str, verb: str, args: dict[str, Any]) -> ToolResult:
        if not FEATURE_FLAGS.get(tool_name, True):
            raise FeatureDisabledError(f"Tool '{tool_name}' is disabled")

        # Dynamic verbs take priority over static ones
        dyn = self._dynamic.get((tool_name, verb))
        if dyn:
            return await self._exec_dynamic(dyn, args)

        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        settings = get_settings()
        if settings.dry_run:
            preview = tool.dry_run(verb, args)
            return ToolResult(success=True, data={"preview": preview}, is_dry_run=True)

        return await tool.execute(verb, args)

    async def _exec_dynamic(self, row: dict, args: dict) -> ToolResult:
        import httpx as _httpx
        from chloe.state.oauth_tokens import load as load_token, refresh as refresh_token
        from chloe.state.db import get_connection
        import json as _json

        namespace: dict = {
            "args": args,
            "ToolResult": ToolResult,
            "httpx": _httpx,
            "load_token": load_token,
            "refresh_token": refresh_token,
            "get_connection": get_connection,
            "json": _json,
            "log": log,
        }
        try:
            exec(compile(row["code"], f"<dynamic:{row['tool']}.{row['verb']}>", "exec"), namespace)
        except Exception as exc:
            return ToolResult(success=False, error=f"Dynamic verb compile error: {exc}")

        run_fn = namespace.get("run")
        if not callable(run_fn):
            return ToolResult(success=False, error="Dynamic verb code must define `async def run(args)`")
        try:
            if asyncio.iscoroutinefunction(run_fn):
                result = await run_fn(args)
            else:
                result = run_fn(args)
            if not isinstance(result, ToolResult):
                result = ToolResult(success=True, data=result)
            return result
        except Exception as exc:
            log.warning("dynamic_verb_exec_failed",
                        tool=row["tool"], verb=row["verb"], error=str(exc))
            return ToolResult(success=False, error=str(exc))

    def get_verb(self, tool_name: str, verb: str) -> ToolVerb | None:
        dyn = self._dynamic.get((tool_name, verb))
        if dyn:
            return ToolVerb(
                name=verb,
                schema=json.loads(dyn["schema"]),
                auth_class=dyn["auth_class"],
                reversibility=dyn["reversibility"],
                description_for_model=dyn["description"],
                description_for_human=dyn["description"][:60],
            )
        tool = self._tools.get(tool_name)
        return tool.get_verb(verb) if tool else None

    def get_tool(self, tool_name: str) -> Tool | None:
        return self._tools.get(tool_name)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry

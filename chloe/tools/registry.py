from typing import Any

from chloe.tools.base import Tool, ToolVerb, ToolResult, FeatureDisabledError
from chloe.config import get_settings, FEATURE_FLAGS
from chloe.observability.logging import get_logger

log = get_logger("tool_registry")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        log.info("tool_registered", name=tool.name, verbs=list(tool.verbs.keys()))

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

        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        settings = get_settings()
        if settings.dry_run:
            preview = tool.dry_run(verb, args)
            return ToolResult(success=True, data={"preview": preview}, is_dry_run=True)

        return await tool.execute(verb, args)

    def get_verb(self, tool_name: str, verb: str) -> ToolVerb | None:
        tool = self._tools.get(tool_name)
        return tool.get_verb(verb) if tool else None


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry

# A-06 · `tools/base.py` + `tools/registry.py` — tool scaffold

## Overview

Implement `chloe/tools/base.py` defining the `Tool` ABC and `ToolVerb` dataclass. Implement `chloe/tools/registry.py` providing the `ToolRegistry` singleton: registers tools, exposes `gemini_tool_declarations()`, `describe_static()`, and `async execute(tool_name, verb, args)`.

## Context

In 1.0, every outreach path calls Discord functions directly. 2.0 introduces a formal tool registry so: (a) the gate has a single `execute()` entry point for all tools, (b) the model receives a consistent description of every available tool, (c) dry-run mode is enforceable globally. The registry also generates the Gemini function-calling declarations and the cached-content tool description block.

## `base.py`

```python
# chloe/tools/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from chloe.actions.schema import AuthClass

@dataclass
class ToolVerb:
    name: str
    schema: dict                      # JSON Schema for args
    auth_class: AuthClass
    reversibility: float              # 0..1
    cost_per_call_usd: float = 0.0
    description_for_model: str = ""   # injected into cached content
    description_for_human: str = ""   # used in audit feed previews
    dry_run: bool = False
    reverse_verb: str | None = None   # name of the verb that undoes this one

@dataclass
class ToolResult:
    success: bool
    data: dict | None = None
    error: str | None = None
    artifact_ref: str | None = None   # if the verb created an artifact
    artifact_kind: str | None = None
    is_dry_run: bool = False

class FeatureDisabledError(Exception):
    pass

class CapExceededError(Exception):
    pass

class Tool(ABC):
    """Abstract base for all Chloe tools."""

    name: str                         # e.g. "spotify"
    verbs: dict[str, ToolVerb]        # populated by subclass __init__

    @abstractmethod
    async def execute(self, verb: str, args: dict[str, Any]) -> ToolResult:
        """Execute the verb with given args."""
        ...

    def dry_run(self, verb: str, args: dict[str, Any]) -> str:
        """
        Return a human-readable preview of what execute would do.
        Must NOT call any external service.
        """
        tv = self.verbs.get(verb)
        if not tv:
            return f"[{self.name}.{verb}] unknown verb"
        args_summary = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        return f"Would {self.name}.{verb}({args_summary})"

    def get_verb(self, verb: str) -> ToolVerb | None:
        return self.verbs.get(verb)
```

## `registry.py`

```python
# chloe/tools/registry.py

from chloe.tools.base import Tool, ToolVerb, ToolResult, FeatureDisabledError
from chloe.config import get_settings, FEATURE_FLAGS
from chloe.observability.logging import get_logger
from typing import Any
import json

log = get_logger("tool_registry")

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool instance. Tool.name must be unique."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        log.info("tool_registered", name=tool.name, verbs=list(tool.verbs.keys()))

    def gemini_tool_declarations(self) -> list[dict]:
        """
        Return the tools block in Gemini function-calling format.
        Excludes tools whose feature flag is False.
        """
        declarations = []
        for name, tool in self._tools.items():
            if not FEATURE_FLAGS.get(name, True):
                continue
            for verb_name, verb in tool.verbs.items():
                declarations.append({
                    "name": f"{name}__{verb_name}",   # double underscore separator
                    "description": verb.description_for_model,
                    "parameters": verb.schema,
                })
        return [{"function_declarations": declarations}]

    def describe_static(self) -> str:
        """
        Return a Markdown block describing all registered tools.
        Injected into the Gemini cached content prefix.
        """
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

    async def execute(
        self, tool_name: str, verb: str, args: dict[str, Any]
    ) -> ToolResult:
        """
        Route execution to the registered tool.
        If DRY_RUN=true, calls dry_run() instead of execute().
        Raises FeatureDisabledError if the tool's feature flag is False.
        """
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

# Singleton
_registry: ToolRegistry | None = None

def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
```

## Dependencies

- A-02 (`Action` schema for auth classes).
- F-02 (config for `FEATURE_FLAGS`, `dry_run`).
- F-09 (logging).

## Testing

### Unit tests — `tests/unit/test_registry.py`

```python
import pytest
import asyncio
from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.tools.registry import ToolRegistry

class StubTool(Tool):
    name = "stub"
    def __init__(self):
        self.verbs = {
            "do_thing": ToolVerb(
                name="do_thing",
                schema={"type": "object", "properties": {}},
                auth_class="kinetic",
                reversibility=0.9,
                description_for_model="Does a thing",
                description_for_human="Do a thing",
            )
        }

    async def execute(self, verb, args):
        return ToolResult(success=True, data={"done": True})

@pytest.fixture
def registry():
    r = ToolRegistry()
    return r

def test_empty_registry_returns_empty_declarations(registry):
    decls = registry.gemini_tool_declarations()
    assert decls == [{"function_declarations": []}]

def test_register_stub_tool(registry):
    registry.register(StubTool())
    decls = registry.gemini_tool_declarations()
    assert len(decls[0]["function_declarations"]) == 1
    assert decls[0]["function_declarations"][0]["name"] == "stub__do_thing"

def test_describe_static_contains_tool_name(registry):
    registry.register(StubTool())
    desc = registry.describe_static()
    assert "stub" in desc
    assert "do_thing" in desc

@pytest.mark.asyncio
async def test_execute_calls_tool(registry):
    registry.register(StubTool())
    result = await registry.execute("stub", "do_thing", {})
    assert result.success
    assert result.data["done"] is True

@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error(registry):
    result = await registry.execute("nonexistent", "verb", {})
    assert not result.success
    assert "Unknown tool" in result.error

@pytest.mark.asyncio
async def test_execute_dry_run_returns_preview(registry, monkeypatch):
    from chloe.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", True)
    registry.register(StubTool())
    result = await registry.execute("stub", "do_thing", {"key": "val"})
    assert result.is_dry_run
    assert "Would" in result.data["preview"]

def test_duplicate_registration_raises(registry):
    registry.register(StubTool())
    with pytest.raises(ValueError):
        registry.register(StubTool())

def test_dry_run_method():
    tool = StubTool()
    preview = tool.dry_run("do_thing", {"x": 1})
    assert "stub" in preview
    assert "do_thing" in preview
```

## Acceptance criteria

- Empty registry returns `[{"function_declarations": []}]` from `gemini_tool_declarations()`.
- Registering a stub tool: `describe_static()` contains the tool name; `execute()` returns the tool's result.
- `DRY_RUN=true` causes `execute()` to return a dry-run preview without calling the real tool.
- Feature-flagged tool raises `FeatureDisabledError`.

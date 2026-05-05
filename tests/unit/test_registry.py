import pytest
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
    return ToolRegistry()


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

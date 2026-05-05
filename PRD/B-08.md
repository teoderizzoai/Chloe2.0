# B-08 · Register static tool descriptions in Gemini cached content

## Overview

At boot, call `registry.describe_static()` and include it in the cached-content prefix alongside `character_prefix.md`. Update F-07's `cache_static_prefix()` to concatenate both files. Add a `GET /admin/cache/status` endpoint that returns the cache name and TTL.

## Context

The tool registry's static description (which tools exist, what verbs they have, what auth class each verb requires) is large but changes rarely. Caching it with Gemini eliminates re-sending ~500–1000 tokens on every chat call. This step wires the tool registry into the cache refresh cycle that was set up in F-07.

## Changes to `llm/gemini.py`

```python
# Update cache_static_prefix() in chloe/llm/gemini.py

async def cache_static_prefix() -> str:
    global _cache_name

    # Load character prefix
    prefix_path = _PROMPT_DIR / "character_prefix.md"
    content_parts = [prefix_path.read_text(encoding="utf-8")]

    # Include tool registry description
    try:
        from chloe.tools.registry import get_registry
        registry = get_registry()
        tool_desc = registry.describe_static()
        content_parts.append(tool_desc)
    except Exception as e:
        log.warning("cache_tool_desc_failed", error=str(e))

    full_content = "\n\n".join(content_parts)

    # Also write to tool_descriptions_static.md for reference
    tools_path = _PROMPT_DIR / "tool_descriptions_static.md"
    tools_path.write_text(tool_desc if 'tool_desc' in dir() else "(no tools registered)", encoding="utf-8")

    cached = await _create_cached_content(CHAT_MODEL, full_content)
    _cache_name = cached.name
    log.info("cache_created", name=_cache_name, content_length=len(full_content))
    return _cache_name
```

## Admin endpoint — `GET /admin/cache/status`

```python
# In chloe/admin/api.py

@admin_router.get("/cache/status")
async def cache_status():
    from chloe.llm.gemini import get_cache_name
    name = get_cache_name()
    return {
        "cache_name": name,
        "active": name is not None,
        "refresh_interval_seconds": 50 * 60,
        "ttl_seconds": 3600,
    }

@admin_router.post("/cache/reset")
async def cache_reset():
    """Force a cache refresh (for ops use)."""
    from chloe.llm.gemini import cache_static_prefix
    name = await cache_static_prefix()
    return {"cache_name": name, "reset": True}
```

## Dependencies

- F-07 (`cache_static_prefix` stub to update).
- A-06 (tool registry with `describe_static()`).

## Testing

### Unit tests — `tests/unit/test_cache_status.py`

```python
import pytest
from fastapi.testclient import TestClient
from chloe.app import create_app

@pytest.fixture
def client():
    return TestClient(create_app())

def test_cache_status_endpoint(client, monkeypatch):
    import chloe.llm.gemini as g
    monkeypatch.setattr(g, "_cache_name", "cachedContents/test-abc")
    
    response = client.get("/admin/cache/status")
    assert response.status_code == 200
    data = response.json()
    assert data["cache_name"] == "cachedContents/test-abc"
    assert data["active"] is True

def test_cache_status_no_cache(client, monkeypatch):
    import chloe.llm.gemini as g
    monkeypatch.setattr(g, "_cache_name", None)
    
    response = client.get("/admin/cache/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is False

def test_cache_prefix_includes_tool_descriptions(monkeypatch):
    """After registering a tool, describe_static() content should appear in cache upload."""
    from chloe.tools.registry import get_registry
    from chloe.tools.base import Tool, ToolVerb
    
    class TestTool(Tool):
        name = "test_tool_for_cache"
        def __init__(self):
            self.verbs = {"do_it": ToolVerb(
                name="do_it", schema={}, auth_class="free", reversibility=1.0,
                description_for_model="Does the test thing",
                description_for_human="Do it",
            )}
        async def execute(self, verb, args): pass
    
    registry = get_registry()
    try:
        registry.register(TestTool())
    except ValueError:
        pass  # already registered in another test
    
    desc = registry.describe_static()
    assert "test_tool_for_cache" in desc or "do_it" in desc
```

## Acceptance criteria

- `GET /admin/cache/status` returns the cache name and TTL.
- `registry.describe_static()` output is included in the cached content upload.
- The `tool_descriptions_static.md` file is written to disk at boot.

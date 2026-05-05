# F-07 · Gemini context caching for the static prefix

## Overview

Add `cache_static_prefix()` to `llm/gemini.py`. This function uploads the concatenation of `character_prefix.md` + `tool_descriptions_static.md` as a Gemini cached content blob with a 1h TTL. Stores the cache name in a module-level variable (stub until `kv` is wired in F-08). Refreshes at boot and every 50 minutes via a background `asyncio` task. All `chat()` calls pass `cached_content=cache_name`.

## Context

Gemini 2.5 Pro supports context caching for prefixes ≥ 1024 tokens. The character prefix + tool registry description is the largest static block in every chat prompt — caching it eliminates repeated token billing and reduces latency. In 1.0, a similar `cache_prefix` mechanism exists at the call level (`_CHLOE_INNER_LIFE` ~150 tokens). 2.0 extends this to the full static prefix (~2000–5000 tokens) using the Gemini `cachedContents` API.

## Interface additions

```python
# In chloe/llm/gemini.py

_cache_name: str | None = None   # module-level stub (replaced by kv in F-08)

async def cache_static_prefix() -> str:
    """
    Upload character_prefix.md + tool_descriptions_static.md as cached content.
    Returns the cache name. Stores it in _cache_name.
    """
    ...

async def start_cache_refresh_task() -> None:
    """
    Background asyncio task. Calls cache_static_prefix() at boot, then every 50 minutes.
    Must be started from loop.py's startup.
    """
    ...

def get_cache_name() -> str | None:
    """Returns the current cache name, or None if not yet cached."""
    return _cache_name
```

## Detailed behaviour

### Uploading

```python
async def cache_static_prefix() -> str:
    global _cache_name
    
    prefix_path = Path(__file__).parent / "prompts" / "character_prefix.md"
    tools_path  = Path(__file__).parent / "prompts" / "tool_descriptions_static.md"
    
    content = prefix_path.read_text() + "\n\n" + tools_path.read_text()
    
    # Gemini cachedContents API
    cached = await client.aio.caches.create(
        model=CHAT_MODEL,
        config=CreateCachedContentConfig(
            contents=[content],
            ttl="3600s",
            display_name="chloe_static_prefix",
        ),
    )
    _cache_name = cached.name
    log.info("cache_created", name=_cache_name)
    return _cache_name
```

### Refresh task

```python
CACHE_REFRESH_INTERVAL = 50 * 60  # 50 minutes in seconds

async def start_cache_refresh_task():
    while True:
        try:
            await cache_static_prefix()
        except Exception as e:
            log.error("cache_refresh_failed", error=str(e))
            # Continue without cache — chat() will pass cached_content=None
        await asyncio.sleep(CACHE_REFRESH_INTERVAL)
```

### Graceful degradation

If `_cache_name` is `None` (cache not yet created or creation failed), `chat()` proceeds without the `cached_content` parameter. The prompt still works — it's just not cached, so costs are higher.

## Prompt files to create

As stubs for now (content filled in later):

`chloe/llm/prompts/character_prefix.md` — static character definition for Chloe. Must be ≥ 1024 tokens for Gemini caching to work. Use a meaningful placeholder that includes:
- Identity assertion ("I am Chloe, not an assistant")
- Auth ladder summary
- Refusal taxonomy sketch

`chloe/llm/prompts/tool_descriptions_static.md` — auto-generated from the tool registry. For now, a placeholder: `# Tool descriptions\n(auto-generated at boot)`

## `/admin/cache/status` endpoint

To be wired in Phase B (B-08). For this step, just ensure `get_cache_name()` is exported.

## Dependencies

- F-05 (`GeminiClient` with `chat()` method).
- F-06 (schemas, for type safety).
- F-09 (logging — use plain logging stub until F-09 exists).

## Testing

### Unit test — cache name stored

```python
@pytest.mark.asyncio
async def test_cache_name_stored_after_create(monkeypatch):
    import chloe.llm.gemini as g
    g._cache_name = None

    async def fake_create(model, config):
        class FakeCache:
            name = "cachedContents/test-123"
        return FakeCache()

    monkeypatch.setattr(g, "_create_cached_content", fake_create)
    name = await g.cache_static_prefix()
    assert name == "cachedContents/test-123"
    assert g.get_cache_name() == "cachedContents/test-123"
```

### Unit test — refresh task calls cache_static_prefix at start

```python
@pytest.mark.asyncio
async def test_refresh_task_calls_cache_on_start(monkeypatch):
    import chloe.llm.gemini as g
    calls = []

    async def fake_cache():
        calls.append(1)
        return "name"

    monkeypatch.setattr(g, "cache_static_prefix", fake_cache)
    
    # Run the task for just one iteration
    task = asyncio.create_task(g.start_cache_refresh_task())
    await asyncio.sleep(0.1)
    task.cancel()
    
    assert len(calls) >= 1
```

### Integration test (`@pytest.mark.integration`)

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_cache_creation():
    import os
    from chloe.llm.gemini import GeminiClient, cache_static_prefix
    name = await cache_static_prefix()
    assert name is not None
    assert len(name) > 0
```

## Acceptance criteria

- `cache_static_prefix()` returns a non-empty string (the cache name).
- `_cache_name` is set after the call.
- `chat()` uses `cached_content=_cache_name` when it's set.
- Integration test hits the real API and confirms the cache name is a non-empty string.

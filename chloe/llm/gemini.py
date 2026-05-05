from __future__ import annotations

from chloe.observability.logging import get_logger

log = get_logger("llm.gemini")

_cache_name: str | None = None


def get_cache_name() -> str | None:
    return _cache_name


async def cache_static_prefix() -> str | None:
    global _cache_name
    try:
        from chloe.tools.registry import get_registry
        registry = get_registry()
        tool_desc = registry.describe_static()
        log.info("cache_static_prefix_built", tool_desc_len=len(tool_desc))
        # Real Gemini caching implemented in F-07; return None until then
        return _cache_name
    except Exception as e:
        log.warning("cache_static_prefix_failed", error=str(e))
        return None


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key

    async def flash(self, prompt_file: str, context: dict, schema) -> dict | None:
        log.warning("gemini_client_stub_called", prompt_file=prompt_file)
        return None

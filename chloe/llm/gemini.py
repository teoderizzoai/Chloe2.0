from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from chloe.observability.logging import get_logger
from chloe.observability.metrics import chloe_llm_errors_total

log = get_logger("llm.gemini")

_cache_name: str | None = None
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PLACEHOLDER_RE = re.compile(r"\{\{([\w.]+)\}\}")


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


def _render_prompt(prompt_file: str, context: dict) -> str:
    """Load a prompt template and substitute {{key}} / {{nested.key}} placeholders."""
    template = (_PROMPTS_DIR / prompt_file).read_text()

    def _resolve(path: str) -> str:
        parts = path.split(".")
        node: Any = context
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part, "")
            else:
                node = getattr(node, part, "")
        return str(node) if node is not None else ""

    return _PLACEHOLDER_RE.sub(lambda m: _resolve(m.group(1)), template)


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")

    async def flash(self, prompt_file: str, context: dict, schema) -> dict | None:
        if not self._api_key:
            log.warning("gemini_no_api_key", prompt_file=prompt_file)
            return None

        try:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(api_key=self._api_key)
            prompt = _render_prompt(prompt_file, context)

            resp = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            import json
            return json.loads(resp.text)
        except Exception as e:
            log.warning("gemini_flash_failed", prompt_file=prompt_file, error=str(e))
            chloe_llm_errors_total.labels(call_type=prompt_file).inc()
            return None


    async def pro_thinking(
        self,
        prompt_file: str,
        context: dict,
        schema,
        thinking_budget: int = 512,
    ) -> dict | None:
        if not self._api_key:
            log.warning("gemini_no_api_key", prompt_file=prompt_file)
            return None

        try:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(api_key=self._api_key)
            prompt = _render_prompt(prompt_file, context)

            resp = await client.aio.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    thinking_config=genai_types.ThinkingConfig(
                        thinking_budget=thinking_budget,
                    ),
                ),
            )
            import json
            return json.loads(resp.text)
        except Exception as e:
            log.warning("gemini_pro_thinking_failed", prompt_file=prompt_file, error=str(e))
            chloe_llm_errors_total.labels(call_type=prompt_file).inc()
            return None


def get_client() -> GeminiClient:
    return GeminiClient()

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

# Pricing per million tokens (conservative estimates, July 2025)
_FLASH_INPUT_USD_PER_M = 0.075
_FLASH_OUTPUT_USD_PER_M = 0.30
_PRO_INPUT_USD_PER_M = 1.25
_PRO_OUTPUT_USD_PER_M = 10.0
_DAILY_BUDGET_USD = float(os.environ.get("CHLOE_DAILY_BUDGET_USD", "5.0"))

# Per-prompt output token budgets — prevent unbounded reflect/witness outputs.
_OUTPUT_BUDGETS: dict[str, int] = {
    "reflect_inner_state.md": 600,
    "reflect_signals.md": 600,
    "witness.md": 300,
    "preflight.md": 800,
    "extract_mentions.md": 400,
    "session_summary.md": 100,
}


def _record_usage(resp, model: str, prompt_file: str) -> None:
    """Extract token counts from response metadata and update Prometheus metrics."""
    try:
        from chloe.observability.metrics import record_llm_call, chloe_budget_usd_today
        from chloe.state.kv import get as kv_get, set as kv_set
        from datetime import date

        usage = getattr(resp, "usage_metadata", None)
        if not usage:
            return

        input_t = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_t = int(getattr(usage, "candidates_token_count", 0) or 0)
        thinking_t = int(getattr(usage, "thoughts_token_count", 0) or 0)

        if "pro" in model:
            usd = (input_t * _PRO_INPUT_USD_PER_M + output_t * _PRO_OUTPUT_USD_PER_M) / 1_000_000
        else:
            usd = (input_t * _FLASH_INPUT_USD_PER_M + output_t * _FLASH_OUTPUT_USD_PER_M) / 1_000_000

        record_llm_call(model, input_t, output_t, thinking_t, usd)

        today = str(date.today())
        kv_key = f"budget:usd:{today}"
        spent_today = float(kv_get(kv_key) or 0.0)
        spent_today += usd
        kv_set(kv_key, spent_today)
        chloe_budget_usd_today.set(spent_today)

        log.info("llm_usage", model=model, prompt=prompt_file,
                 input_tokens=input_t, output_tokens=output_t, thinking_tokens=thinking_t,
                 usd=round(usd, 6), usd_today=round(spent_today, 4))

        if spent_today > _DAILY_BUDGET_USD:
            log.warning("daily_budget_exceeded",
                        spent=round(spent_today, 4), limit=_DAILY_BUDGET_USD)
    except Exception as exc:
        log.debug("record_usage_failed", error=str(exc))


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

    async def flash(
        self,
        prompt_file: str,
        context: dict,
        schema,
        max_output_tokens: int | None = None,
    ) -> dict | None:
        if not self._api_key:
            log.warning("gemini_no_api_key", prompt_file=prompt_file)
            return None

        try:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(api_key=self._api_key)
            prompt = _render_prompt(prompt_file, context)

            budget = max_output_tokens or _OUTPUT_BUDGETS.get(prompt_file)
            cfg_kwargs: dict = {
                "response_mime_type": "application/json",
                "response_schema": schema,
            }
            if budget:
                cfg_kwargs["max_output_tokens"] = budget

            resp = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(**cfg_kwargs),
            )
            _record_usage(resp, "gemini-2.5-flash", prompt_file)
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
            _record_usage(resp, "gemini-2.5-pro", prompt_file)
            import json
            return json.loads(resp.text)
        except Exception as e:
            log.warning("gemini_pro_thinking_failed", prompt_file=prompt_file, error=str(e))
            chloe_llm_errors_total.labels(call_type=prompt_file).inc()
            return None


def get_client() -> GeminiClient:
    return GeminiClient()

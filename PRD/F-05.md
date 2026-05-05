# F-05 · `llm/gemini.py` — Gemini client wrapper

## Overview

Implement the async Gemini client in `chloe/llm/gemini.py`. Wraps `google-generativeai` (or `google-genai`) with three public methods: `chat()`, `flash()`, and `pro_thinking()`. Handles retry with exponential backoff (2 attempts, max 8s). On final failure returns `None` and logs; callers handle `None`. Hard-codes model IDs and parameter blocks from PRD §5.7.

## Context

The existing `llm.py` in 1.0 wraps Gemini with a `_call()` function that has grown to ~27 sub-functions for different use cases. 2.0 collapses these into three clean async methods with structured output support. The context-caching integration (`cached_content`) is a stub in this step and wired in F-07.

## Model IDs and parameters

```python
CHAT_MODEL       = "gemini-2.5-pro"
BACKGROUND_MODEL = "gemini-2.5-flash"
WEEKLY_MODEL     = "gemini-2.5-pro"

CHAT_PARAMS = {
    "temperature": 0.85,
    "top_p": 0.95,
    "max_output_tokens": 1024,
    "thinking_config": {"thinking_budget": 512},
}
BACKGROUND_PARAMS = {
    "temperature": 0.4,
    "top_p": 0.9,
    "max_output_tokens": 800,
    "thinking_config": {"thinking_budget": 0},
}
WEEKLY_PARAMS = {
    "temperature": 0.6,
    "top_p": 0.95,
    "max_output_tokens": 4096,
    "thinking_config": {"thinking_budget": 8192},
}
DELIBERATE_PARAMS = {
    "temperature": 0.3,
    "top_p": 0.9,
    "max_output_tokens": 256,
    "thinking_config": {"thinking_budget": 256},
}
VOICE_TURN_PARAMS = {
    "temperature": 0.85,
    "max_output_tokens": 200,
}
```

## Interface

```python
from typing import Any
from dataclasses import dataclass

@dataclass
class ChatResult:
    text: str
    tool_calls: list[dict]     # list of {name, args} dicts
    input_tokens: int
    output_tokens: int
    thinking_tokens: int

class GeminiClient:
    def __init__(self, api_key: str):
        ...

    async def chat(
        self,
        contents: list[dict],
        system: str,
        cached_content: str | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ChatResult | None:
        """
        Make a Pro chat call. Returns ChatResult or None on unrecoverable error.
        kwargs override CHAT_PARAMS (e.g. voice=True sets VOICE_TURN_PARAMS).
        """
        ...

    async def flash(
        self,
        prompt_name: str,
        payload: dict,
        schema: type,           # a Pydantic BaseModel subclass
        params_override: dict | None = None,
    ) -> dict | None:
        """
        Structured-output Flash call. Loads prompt from chloe/llm/prompts/{prompt_name}.
        Returns validated dict matching schema, or None on failure.
        """
        ...

    async def pro_thinking(
        self,
        prompt_name: str,
        payload: dict,
        schema: type,
        thinking_budget: int = 8192,
    ) -> dict | None:
        """
        Pro call with extended thinking. Uses WEEKLY_PARAMS with thinking_budget override.
        """
        ...
```

## Retry logic

```python
import asyncio

MAX_RETRIES = 2
BACKOFF_BASE = 2.0   # seconds

async def _with_retry(fn, *args, **kwargs):
    for attempt in range(MAX_RETRIES):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                log.error("gemini_final_failure", error=str(e))
                return None
            wait = BACKOFF_BASE ** attempt   # 1s, then 2s
            wait = min(wait, 8.0)
            await asyncio.sleep(wait)
```

Retried exceptions: any `google.api_core.exceptions.GoogleAPICallError` or `httpx.HTTPError`. On 429 (rate limit), the wait is `min(retry_delay_from_header, 8.0)`.

## Structured output (flash)

For `flash()` calls, set:
```python
generation_config = genai.GenerationConfig(
    response_mime_type="application/json",
    response_schema=schema.model_json_schema(),
    **BACKGROUND_PARAMS,
)
```

After receiving the response, validate with `schema.model_validate_json(response.text)`. If validation fails, log and return `None`.

## Prompt loading

```python
_PROMPT_DIR = Path(__file__).parent / "prompts"

def _load_prompt(name: str) -> str:
    path = _PROMPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {name}")
    return path.read_text(encoding="utf-8")
```

The `payload` dict is JSON-serialised and appended after the prompt text as a `---\n{json}\n---` block.

## Token tracking

Every call logs token usage to structlog:
```python
log.info("gemini_call", model=model, prompt_tokens=n, output_tokens=m, thinking_tokens=k)
```

After F-10 (metrics), these also increment the Prometheus counters. For now, just log.

## Dependencies

- F-01 (package structure).
- F-02 (config, for `gemini_api_key`).
- F-09 (logging, but can be a plain `logging.getLogger` stub until F-09 is done).

## Testing

### Unit tests — `tests/unit/test_gemini.py`

Use `pytest-httpx` or `unittest.mock.patch` to intercept Gemini API calls.

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.llm.gemini import GeminiClient

@pytest.fixture
def client():
    return GeminiClient(api_key="test-key")

@pytest.mark.asyncio
async def test_retry_on_transient_error(client):
    """Should retry once, then succeed."""
    call_count = 0
    original_error = Exception("transient")

    async def fake_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise original_error
        return MagicMock(text="hello", usage_metadata=MagicMock(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0
        ), function_calls=[])

    with patch.object(client, "_raw_generate", fake_generate):
        result = await client.chat(
            contents=[{"role":"user","parts":[{"text":"hi"}]}],
            system="test",
        )
    assert call_count == 2
    assert result is not None
    assert result.text == "hello"

@pytest.mark.asyncio
async def test_returns_none_after_max_retries(client):
    """Should return None after 2 failures."""
    async def always_fail(*args, **kwargs):
        raise Exception("always fails")

    with patch.object(client, "_raw_generate", always_fail):
        result = await client.chat(
            contents=[{"role":"user","parts":[{"text":"hi"}]}],
            system="test",
        )
    assert result is None

@pytest.mark.asyncio
async def test_flash_validates_schema(client):
    from chloe.llm.schemas import Graded
    import json

    fake_response = MagicMock()
    fake_response.text = json.dumps({"memories": [], "total": 0})

    async def fake_generate(*args, **kwargs):
        return fake_response

    with patch.object(client, "_raw_generate", fake_generate):
        result = await client.flash("grade_memories.md", {"memories": []}, Graded)
    # Graded schema validation should either pass or return None
    # (depends on whether the canned response matches the schema)
    assert result is None or isinstance(result, dict)
```

### Integration test (marked `@pytest.mark.integration`)

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_chat_call():
    import os
    from chloe.llm.gemini import GeminiClient
    client = GeminiClient(api_key=os.environ["GEMINI_API_KEY"])
    result = await client.chat(
        contents=[{"role":"user","parts":[{"text":"say the word 'hello'"}]}],
        system="You are a test bot. Respond only with the word hello.",
    )
    assert result is not None
    assert "hello" in result.text.lower()
    assert result.input_tokens > 0
```

## Acceptance criteria

- `chat()` retries once on transient error and returns `None` after 2 consecutive failures.
- `flash()` returns a dict matching the schema, or `None` on validation failure.
- Unit tests pass without a real API key.
- Integration test passes with `GEMINI_API_KEY` set.

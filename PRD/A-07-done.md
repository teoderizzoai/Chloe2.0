# A-07 · `tools/messages.py` — messages tool

## Overview

Implement `chloe/tools/messages.py` wrapping the existing Discord DM send functionality as a formal tool with verbs `send_text(body)` and `send_voice(audio_file)`. Auth: `kinetic`. For now, delegates to the existing Discord DM bridge from 1.0. Push (APNs/FCM) is wired in Phase F.

## Context

In 1.0, `_send_autonomous_outreach()` in `chloe.py` calls Discord functions directly — bypassing any gate, logging nothing. In 2.0, every message Chloe sends must pass through the gate and appear in the audit feed. This step wraps the existing Discord send in the `Tool` interface so that the gate (A-08) can call it uniformly with all other tools.

The Discord send logic currently lives in `discord_bot.py` → `chloe.send_message()`. This step does NOT rewrite that logic — it wraps it.

## Implementation

```python
# chloe/tools/messages.py

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("tool.messages")

class MessagesTool(Tool):
    name = "messages"

    def __init__(self, send_callback=None):
        """
        send_callback: async callable(body: str) -> bool
        In production, this is wired to discord_bot.send_dm() or APNs (Phase F).
        In tests, pass a mock.
        """
        self._send_callback = send_callback
        self.verbs = {
            "send_text": ToolVerb(
                name="send_text",
                schema={
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "Message text to send"},
                    },
                    "required": ["body"],
                },
                auth_class="kinetic",
                reversibility=0.0,   # cannot unsend a message
                cost_per_call_usd=0.0,
                description_for_model=(
                    "Send a text message to Teo via the primary channel (Discord/push). "
                    "Use sparingly — this is a real message that arrives on his phone."
                ),
                description_for_human="Send a text message",
            ),
            "send_voice": ToolVerb(
                name="send_voice",
                schema={
                    "type": "object",
                    "properties": {
                        "audio_file": {"type": "string", "description": "Path to audio file"},
                    },
                    "required": ["audio_file"],
                },
                auth_class="kinetic",
                reversibility=0.0,
                cost_per_call_usd=0.0,
                description_for_model="Send a voice message. Use rarely.",
                description_for_human="Send a voice message",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        settings = get_settings()

        if verb == "send_text":
            body = args.get("body", "").strip()
            if not body:
                return ToolResult(success=False, error="body is required")

            if not self._send_callback:
                log.warning("messages_no_callback", verb=verb)
                return ToolResult(success=False, error="No send callback registered")

            if not settings.discord_enabled:
                log.info("messages_discord_disabled", verb=verb)
                return ToolResult(success=False, error="Discord is disabled")

            try:
                success = await self._send_callback(body)
                log.info("messages_sent", verb=verb, length=len(body))
                return ToolResult(success=bool(success), data={"sent": True})
            except Exception as e:
                log.error("messages_send_failed", verb=verb, error=str(e))
                return ToolResult(success=False, error=str(e))

        elif verb == "send_voice":
            # Voice send not implemented until Phase F
            return ToolResult(
                success=False,
                error="send_voice not yet implemented (Phase F)",
                is_dry_run=False,
            )

        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "send_text":
            body = args.get("body", "")
            return f"Would send: {body[:100]}"
        return super().dry_run(verb, args)
```

## Registration

In `loop.py` (or a `tools/__init__.py` boot function), register the tool:

```python
from chloe.tools.messages import MessagesTool
from chloe.tools.registry import get_registry

def register_all_tools(discord_send_callback=None):
    registry = get_registry()
    registry.register(MessagesTool(send_callback=discord_send_callback))
    # ... other tools registered in later steps
```

## Discord callback

The existing `discord_bot.py` exposes a `send_dm(message: str) -> bool` function. Wire it as the `send_callback` in the lifespan setup in `loop.py`. If `DISCORD_ENABLED=false`, the callback is `None` and `execute()` returns an error (which the gate records).

## Dependencies

- A-06 (`Tool`, `ToolVerb`, `ToolResult` base classes and `ToolRegistry`).
- F-02 (config for `discord_enabled`).
- F-09 (logging).
- Existing 1.0 `discord_bot.py` (not modified — just wrapped).

## Testing

### Unit tests — `tests/unit/test_messages_tool.py`

```python
import pytest
import asyncio
from chloe.tools.messages import MessagesTool

@pytest.fixture
def tool_with_mock():
    sent = []
    async def mock_send(body):
        sent.append(body)
        return True
    tool = MessagesTool(send_callback=mock_send)
    return tool, sent

@pytest.mark.asyncio
async def test_dry_run_no_api_call():
    tool = MessagesTool(send_callback=None)
    preview = tool.dry_run("send_text", {"body": "hello world"})
    assert "Would send" in preview
    assert "hello world" in preview

@pytest.mark.asyncio
async def test_dry_run_does_not_call_callback():
    called = []
    async def mock_send(body):
        called.append(body)
        return True
    tool = MessagesTool(send_callback=mock_send)
    tool.dry_run("send_text", {"body": "test"})
    assert len(called) == 0

@pytest.mark.asyncio
async def test_send_text_calls_callback(tool_with_mock):
    tool, sent = tool_with_mock
    result = await tool.execute("send_text", {"body": "hello"})
    assert result.success
    assert len(sent) == 1
    assert sent[0] == "hello"

@pytest.mark.asyncio
async def test_send_text_empty_body_fails(tool_with_mock):
    tool, sent = tool_with_mock
    result = await tool.execute("send_text", {"body": ""})
    assert not result.success
    assert "body is required" in result.error

@pytest.mark.asyncio
async def test_send_text_no_callback_fails():
    tool = MessagesTool(send_callback=None)
    result = await tool.execute("send_text", {"body": "hello"})
    assert not result.success

@pytest.mark.asyncio
async def test_send_voice_returns_not_implemented(tool_with_mock):
    tool, _ = tool_with_mock
    result = await tool.execute("send_voice", {"audio_file": "test.mp3"})
    assert not result.success
    assert "Phase F" in result.error

def test_verb_auth_is_kinetic():
    tool = MessagesTool()
    assert tool.verbs["send_text"].auth_class == "kinetic"
    assert tool.verbs["send_voice"].auth_class == "kinetic"
```

## Acceptance criteria

- `send_text` in dry-run mode returns `"Would send: <body>"` without hitting Discord.
- `send_text` with a mock callback delivers the message.
- Empty body returns an error result.
- `send_voice` returns "not implemented" until Phase F.

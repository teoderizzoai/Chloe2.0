# B-07 · Update chat path to include `audit_recent` in the dynamic suffix

## Overview

In `channels/chat_api.py`, assemble `audit_recent = audit.feed_text(n=10)` and include it in the per-call dynamic suffix passed to Gemini Pro. Write the corresponding `chat_system.md` dynamic stub that references the `{audit_recent}` block.

## Context

The chat prompt is currently assembled in 1.0's `llm.chat()` function. In 2.0, the dynamic suffix is assembled in `chat_api.py` and injected as `system_instruction` alongside the cached static prefix. The `audit_recent` block lets Chloe see what she has recently done — so she can naturally reference "I queued something for you earlier" or hold back if she's already sent 3 messages today.

## Prompt assembly pattern

```python
# In chloe/channels/chat_api.py

async def build_dynamic_suffix(person_id: str) -> str:
    """
    Build the per-call dynamic system suffix.
    This is passed as system_instruction to Gemini Pro alongside the cached prefix.
    """
    from chloe.actions import audit
    from chloe.affect.dims import load_affect, tone_block   # (implemented in E-03/E-05)
    from chloe.state import kv
    import json
    
    # Audit feed (always included)
    actions = await audit.recent(n=20)
    audit_text = audit.feed_text(actions, n=10)
    
    # Affect state (stub until E-03)
    affect_text = kv.get("mood_label", "unknown")   # 1.0 compat key
    
    # Build suffix
    parts = [
        f"## Recent actions\n{audit_text}",
        f"## Current affect\n{affect_text}",
        # More blocks added in later phases
    ]
    return "\n\n".join(parts)
```

## `chat_system.md` stub

Create `chloe/llm/prompts/chat_system.md` with the dynamic suffix template:

```markdown
## Context for this conversation turn

### What you've done recently
{audit_recent}

### How you feel right now
{affect_label}

### Active leash constraints
{leash_summary}
```

This file is documentation / prompt engineering reference. The actual interpolation happens in `build_dynamic_suffix()` in Python (not template substitution — the format is for human readability and future prompt engineers).

## Gemini call update

In the existing chat call path (wherever `llm.chat()` or equivalent is called), add the dynamic suffix:

```python
dynamic_suffix = await build_dynamic_suffix(person_id)

result = await gemini_client.chat(
    contents=history_payload,
    system=dynamic_suffix,        # per-call suffix
    cached_content=get_cache_name(),  # static cached prefix
    tools=registry.gemini_tool_declarations(),
    **CHAT_PARAMS,
)
```

## Dependencies

- A-03 (`audit.recent`, `audit.feed_text`).
- F-07 (Gemini context cache for the static prefix).
- F-05 (GeminiClient `chat()` method).

## Testing

### Integration test — `tests/integration/test_chat_audit_context.py`

```python
import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock
from chloe.state.db import migrate, close
from chloe.actions.schema import Action
from chloe.actions import audit

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

@pytest.mark.asyncio
async def test_audit_context_included_after_action():
    """
    After executing one notes.append action, the next build_dynamic_suffix
    call should contain that action's intent.
    """
    # Insert an action
    a = Action(
        tool="notes", verb="append",
        intent="added a thought about the ocean",
        preview="Append to notes",
        authorization="kinetic",
        state="executed",
    )
    await audit.append(a)
    
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix(person_id="teo")
    
    assert "notes" in suffix
    assert "append" in suffix or "ocean" in suffix

@pytest.mark.asyncio
async def test_build_dynamic_suffix_returns_string():
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix(person_id="teo")
    assert isinstance(suffix, str)
    assert len(suffix) > 0

@pytest.mark.asyncio
async def test_audit_in_suffix_has_correct_format():
    a = Action(
        tool="spotify", verb="queue_track",
        intent="queue calming music",
        preview="Queue track",
        authorization="kinetic",
        state="executed",
    )
    await audit.append(a)
    
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix("teo")
    
    assert "spotify" in suffix
    assert "queue_track" in suffix
```

### Unit test for `build_dynamic_suffix` isolation

```python
@pytest.mark.asyncio
async def test_suffix_graceful_when_no_actions():
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix("teo")
    # Should not raise even with empty audit feed
    assert "no recent" in suffix.lower() or "Recent actions" in suffix
```

## Acceptance criteria

- Integration test: after one `notes.append` action, the next chat call's dynamic suffix contains that action's intent string.
- `build_dynamic_suffix` returns a non-empty string even with an empty audit feed.
- The dynamic suffix is passed as `system_instruction` (not merged into `cached_content`).

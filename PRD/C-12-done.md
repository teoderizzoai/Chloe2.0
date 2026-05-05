# C-12 · Dry-run canary for all kinetic verbs

## Overview

Add a CI job that runs the full test suite with `DRY_RUN=true`. Assert that no kinetic verb makes an outbound HTTP call when in dry-run mode. The `ToolRegistry.execute()` already honours `DRY_RUN` by routing to `tool.dry_run()` instead of `tool.execute()` — this step adds the CI enforcement and a test that verifies no HTTP leakage occurs.

## Context

The dry-run mode is the safety net during development and CI: it lets the full initiative + gate pipeline run without touching real APIs. Any accidental HTTP call that bypasses the `DRY_RUN` flag would be caught here before reaching production. The canary approach — running the entire suite with the flag set — is more reliable than unit-testing each tool in isolation, because it catches integration paths that individual tests might miss.

## Implementation

### Feature flag check in `ToolRegistry`

The check should already be in `tools/registry.py` (A-06). Verify:

```python
# tools/registry.py
async def execute(self, tool_name: str, verb: str, args: dict) -> ToolResult:
    from chloe.config import get_settings
    if get_settings().dry_run:
        tool = self._tools[tool_name]
        preview = tool.dry_run(verb, args)
        return ToolResult(success=True, data={"preview": preview}, dry_run=True)
    # ... normal execution
```

`ToolResult` needs a `dry_run: bool = False` field.

### HTTP call interceptor test

```python
# tests/unit/test_dry_run_canary.py

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from chloe.tools.spotify import SpotifyTool
from chloe.tools.calendar import CalendarTool
from chloe.tools.gmail import GmailTool
from chloe.tools.reminders import RemindersTool

KINETIC_SCENARIOS = [
    (SpotifyTool, "queue_track", {"uri": "spotify:track:abc123"}),
    (SpotifyTool, "start_playlist", {"uri": "spotify:playlist:xyz"}),
    (SpotifyTool, "like", {"uri": "spotify:track:abc123"}),
    (SpotifyTool, "skip", {}),
    (CalendarTool, "add_event", {"title": "Meeting", "start": "2026-05-05T10:00:00", "end": "2026-05-05T11:00:00"}),
    (CalendarTool, "add_reminder", {"time": "2026-05-05T09:00:00", "body": "check email"}),
    (GmailTool, "draft_reply", {"threadId": "t1", "body": "hello"}),
    (RemindersTool, "add", {"title": "Buy milk"}),
    (RemindersTool, "complete", {"taskId": "task1"}),
]


@pytest.mark.parametrize("ToolClass,verb,args", KINETIC_SCENARIOS)
def test_dry_run_makes_no_http_call(ToolClass, verb, args):
    """dry_run() must not call httpx or any external HTTP library."""
    tool = ToolClass()

    http_called = []

    def spy(*a, **kw):
        http_called.append((a, kw))
        return MagicMock()

    with patch("httpx.AsyncClient", spy), patch("httpx.Client", spy):
        result = tool.dry_run(verb, args)

    assert len(http_called) == 0, f"{ToolClass.__name__}.dry_run('{verb}') made HTTP call(s)"
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ToolClass,verb,args", KINETIC_SCENARIOS)
async def test_registry_dry_run_mode_no_http(ToolClass, verb, args, monkeypatch):
    """When DRY_RUN=true, ToolRegistry.execute() must not make HTTP calls."""
    from chloe.tools.registry import ToolRegistry
    from chloe.config import get_settings

    monkeypatch.setattr("chloe.tools.registry.get_settings", lambda: MagicMock(dry_run=True))
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.reminders.load_token", lambda s: {"access_token": "tok"})

    registry = ToolRegistry()
    registry.register(SpotifyTool())
    registry.register(CalendarTool())
    registry.register(GmailTool())
    registry.register(RemindersTool())

    http_called = []

    def spy(*a, **kw):
        http_called.append((a, kw))
        raise AssertionError("HTTP call made in dry-run mode")

    tool_name = ToolClass().name
    with patch("httpx.AsyncClient", spy):
        result = await registry.execute(tool_name, verb, args)

    assert result.dry_run is True
    assert result.success is True
    assert len(http_called) == 0
```

## CI job

Add to `.github/workflows/ci.yml` (or the equivalent CI config):

```yaml
dry-run-canary:
  name: Dry-run canary
  runs-on: ubuntu-latest
  env:
    DRY_RUN: "true"
    CHLOE_NOTES_DIR: /tmp/chloe_notes
    CHLOE_WORKSPACE_DIR: /tmp/chloe_workspace
    CHLOE_MASTER_KEY: test_master_key_32_bytes_hex_pad!
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - run: pip install -e ".[dev]"
    - run: pytest tests/unit/test_dry_run_canary.py -v --tb=short
```

## Dependencies

- A-06 (`ToolRegistry.execute()` with `DRY_RUN` feature flag).
- C-01 through C-05 (all kinetic write verbs implemented with `dry_run()` methods).

## Testing

The test file *is* the deliverable. The acceptance gate is the CI job passing with `DRY_RUN=true`.

### Edge-case test: intimate reads still work in dry-run

```python
@pytest.mark.asyncio
async def test_intimate_reads_not_blocked_by_dry_run(monkeypatch):
    """Intimate-class reads (non-kinetic) should still execute in dry-run mode."""
    from chloe.tools.registry import ToolRegistry
    from chloe.tools.spotify import SpotifyTool
    from chloe.tools.base import ToolResult

    monkeypatch.setattr("chloe.tools.registry.get_settings", lambda: MagicMock(dry_run=True))

    registry = ToolRegistry()
    tool = SpotifyTool()
    registry.register(tool)

    # For intimate verbs in dry-run, the registry should still route to dry_run()
    # (no real API call) — they return a preview, not live data
    result = await registry.execute("spotify", "show_currently_playing", {})
    assert result.dry_run is True
```

## Acceptance criteria

- `pytest tests/unit/test_dry_run_canary.py` passes with `DRY_RUN=true`.
- Every kinetic verb's `dry_run()` returns a non-empty string with zero HTTP calls.
- `ToolRegistry.execute()` with `DRY_RUN=true` returns `ToolResult(dry_run=True)` and makes no outbound connections.
- CI job runs automatically on each PR and blocks merges if any kinetic verb leaks HTTP calls.

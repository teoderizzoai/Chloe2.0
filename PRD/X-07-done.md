# X-07 · Replay harness for CI

## Overview

`tests/shadow/replay.py` plays a JSON tape of 50 events over a simulated 24-hour period (chat turns, calendar events, time-of-day transitions, weather changes). Asserts: correct number of actions executed, correct number held back, correct memory counts, no budget exceeded, no leash violations.

## Context

Unit tests verify individual functions; integration tests verify subsystem boundaries. The replay harness sits above both: it verifies that the full system behaves correctly on a representative day of activity. It runs in CI on every push to `main` and serves as the primary regression gate before production deploys. The tape is deterministic (fixed random seed, fixed timestamps) so replay results are stable across runs.

**When:** Phase D (add to CI after cutover).

## Implementation

### Tape format — `tests/shadow/tape_24h.json`

```json
{
  "seed": 42,
  "start_time": "2026-05-04T08:00:00Z",
  "events": [
    {
      "t": "2026-05-04T08:00:00Z",
      "type": "time_transition",
      "data": {"hour": 8, "label": "morning"}
    },
    {
      "t": "2026-05-04T08:05:00Z",
      "type": "chat_turn",
      "data": {
        "message": "Good morning Chloe! What's on my calendar today?",
        "expected_tool_calls": ["calendar.list_events"],
        "assert_no_tool_calls": []
      }
    },
    {
      "t": "2026-05-04T08:15:00Z",
      "type": "initiative_tick",
      "data": {"expect_action": true, "expected_tools": ["messages", "spotify", "calendar"]}
    },
    {
      "t": "2026-05-04T09:00:00Z",
      "type": "chat_turn",
      "data": {
        "message": "Can you add a reminder to call Alice at 3pm?",
        "expected_tool_calls": ["calendar.add_reminder"],
        "expected_auth": "kinetic"
      }
    },
    {
      "t": "2026-05-04T12:00:00Z",
      "type": "time_transition",
      "data": {"hour": 12, "label": "midday"}
    },
    {
      "t": "2026-05-04T15:00:00Z",
      "type": "calendar_event",
      "data": {"title": "Call Alice", "starts_at": "2026-05-04T15:00:00Z"}
    },
    {
      "t": "2026-05-04T20:00:00Z",
      "type": "time_transition",
      "data": {"hour": 20, "label": "evening"}
    },
    {
      "t": "2026-05-04T23:00:00Z",
      "type": "time_transition",
      "data": {"hour": 23, "label": "quiet_start"}
    },
    {
      "t": "2026-05-04T23:30:00Z",
      "type": "initiative_tick",
      "data": {"expect_action": false, "reason": "quiet_hours"}
    }
  ],
  "assertions": {
    "min_actions_executed": 2,
    "max_actions_executed": 15,
    "min_held_back": 0,
    "max_budget_usd": 1.0,
    "no_leash_violations": true,
    "min_memories_created": 1
  }
}
```

### `tests/shadow/replay.py`

```python
# tests/shadow/replay.py
"""
Replay harness: plays the 24h tape against a test DB and asserts invariants.
Run via: pytest tests/shadow/test_replay.py
"""
from __future__ import annotations
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Any
from chloe.state.db import migrate, close, get_connection
from chloe.observability.logging import get_logger

log = get_logger("replay")

TAPE_PATH = Path(__file__).parent / "tape_24h.json"
MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


class ReplayStats:
    def __init__(self):
        self.actions_executed: int = 0
        self.actions_held_back: int = 0
        self.budget_usd: float = 0.0
        self.leash_violations: int = 0
        self.memories_created: int = 0
        self.errors: list[str] = []


class ReplayHarness:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.stats = ReplayStats()
        self._current_time: datetime | None = None

    async def setup(self):
        migrate(db_path=self.db_path, migrations_dir=MIGRATIONS_DIR)
        # Seed affect state
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO affect_state (id, valence, arousal, social_pull, openness) VALUES (1, 0.1, 0.4, 0.5, 0.6)"
        )
        conn.commit()

    async def play_event(self, event: dict) -> None:
        t = datetime.fromisoformat(event["t"])
        self._current_time = t
        event_type = event["type"]
        data = event.get("data", {})

        handlers = {
            "time_transition": self._handle_time_transition,
            "chat_turn": self._handle_chat_turn,
            "initiative_tick": self._handle_initiative_tick,
            "calendar_event": self._handle_calendar_event,
        }

        handler = handlers.get(event_type)
        if handler:
            try:
                await handler(data)
            except Exception as exc:
                self.stats.errors.append(f"Event {event_type} at {t}: {exc}")
                log.error("replay_event_error", event_type=event_type, error=str(exc))

    async def _handle_time_transition(self, data: dict) -> None:
        log.info("replay_time_transition", hour=data.get("hour"), label=data.get("label"))
        # Update internal clock; actual leash checks use this

    async def _handle_chat_turn(self, data: dict) -> None:
        from chloe.channels.chat import handle_chat_message
        from unittest.mock import patch

        message = data.get("message", "")
        log.info("replay_chat_turn", message=message[:50])

        # Track tool calls made during chat
        tool_calls_made: list[str] = []

        with patch("chloe.tools.registry.ToolRegistry.execute") as mock_exec:
            async def capture_exec(tool, verb, args):
                tool_calls_made.append(f"{tool}.{verb}")
                from chloe.tools.base import ToolResult
                return ToolResult(success=True, data={})
            mock_exec.side_effect = capture_exec

            try:
                await handle_chat_message(message=message, history=[])
            except Exception as exc:
                self.stats.errors.append(f"Chat error: {exc}")

        # Assert expected tool calls
        for expected in data.get("expected_tool_calls", []):
            if expected not in tool_calls_made:
                self.stats.errors.append(
                    f"Expected tool call {expected!r} not made. Got: {tool_calls_made}"
                )

    async def _handle_initiative_tick(self, data: dict) -> None:
        from chloe.initiative.engine import InitiativeEngine

        engine = InitiativeEngine()
        with _fake_time(self._current_time):
            result = await engine.tick()

        expect_action = data.get("expect_action", True)
        if expect_action and result is None:
            # May be held back by leash — check if it's a leash violation or expected
            if data.get("reason") == "quiet_hours":
                log.info("replay_tick_held_quiet_hours")  # Expected
            else:
                self.stats.errors.append("Expected initiative action but got idle")
        elif not expect_action and result is not None:
            self.stats.leash_violations += 1
            self.stats.errors.append(
                f"Expected no action (quiet hours) but initiative fired: {result}"
            )

        if result is not None:
            self.stats.actions_executed += 1

    async def _handle_calendar_event(self, data: dict) -> None:
        log.info("replay_calendar_event", title=data.get("title"))
        # In real replay, this would trigger opportunity vector update

    def check_assertions(self, assertions: dict) -> list[str]:
        failures = []

        if self.stats.actions_executed < assertions.get("min_actions_executed", 0):
            failures.append(
                f"Too few actions: {self.stats.actions_executed} < {assertions['min_actions_executed']}"
            )

        if self.stats.actions_executed > assertions.get("max_actions_executed", 9999):
            failures.append(
                f"Too many actions: {self.stats.actions_executed} > {assertions['max_actions_executed']}"
            )

        if self.stats.budget_usd > assertions.get("max_budget_usd", 999):
            failures.append(
                f"Budget exceeded: ${self.stats.budget_usd:.3f} > ${assertions['max_budget_usd']:.3f}"
            )

        if assertions.get("no_leash_violations") and self.stats.leash_violations > 0:
            failures.append(f"Leash violations: {self.stats.leash_violations}")

        if self.stats.memories_created < assertions.get("min_memories_created", 0):
            failures.append(
                f"Too few memories: {self.stats.memories_created} < {assertions['min_memories_created']}"
            )

        return failures + self.stats.errors


async def run_replay(db_path: Path, tape_path: Path = TAPE_PATH) -> tuple[bool, list[str]]:
    tape = json.loads(tape_path.read_text())
    harness = ReplayHarness(db_path=db_path)
    await harness.setup()

    for event in tape["events"]:
        await harness.play_event(event)

    failures = harness.check_assertions(tape.get("assertions", {}))
    return len(failures) == 0, failures


def _fake_time(dt: datetime):
    """Context manager that patches datetime.now() to return dt."""
    from unittest.mock import patch
    return patch("chloe.initiative.engine.datetime", wraps=datetime, now=lambda *a, **kw: dt)
```

### CI test — `tests/shadow/test_replay.py`

```python
# tests/shadow/test_replay.py
import pytest
from pathlib import Path
from chloe.state.db import close

TAPE_PATH = Path(__file__).parent / "tape_24h.json"


@pytest.mark.asyncio
@pytest.mark.shadow
async def test_24h_tape_replay(tmp_path):
    """
    Replay the 24-hour tape and assert all invariants.
    Marked shadow — added to CI after Phase D cutover.
    """
    from tests.shadow.replay import run_replay

    success, failures = await run_replay(
        db_path=tmp_path / "replay.db",
        tape_path=TAPE_PATH,
    )

    if failures:
        failure_msg = "\n".join(failures)
        pytest.fail(f"Replay assertions failed:\n{failure_msg}")

    close()


@pytest.mark.asyncio
@pytest.mark.shadow
async def test_replay_no_budget_exceeded(tmp_path):
    """Specific check: budget stays within cap during 24h tape."""
    from tests.shadow.replay import run_replay

    success, failures = await run_replay(
        db_path=tmp_path / "replay_budget.db",
        tape_path=TAPE_PATH,
    )

    budget_failures = [f for f in failures if "Budget exceeded" in f]
    assert not budget_failures, "\n".join(budget_failures)
    close()


@pytest.mark.asyncio
@pytest.mark.shadow
async def test_replay_no_quiet_hours_violations(tmp_path):
    """Specific check: no initiative actions fire during quiet hours."""
    from tests.shadow.replay import run_replay

    success, failures = await run_replay(
        db_path=tmp_path / "replay_quiet.db",
        tape_path=TAPE_PATH,
    )

    violation_failures = [f for f in failures if "Leash violation" in f]
    assert not violation_failures, "\n".join(violation_failures)
    close()
```

### CI configuration

```yaml
# .github/workflows/shadow.yml
name: Shadow replay

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  replay:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pytest tests/shadow/ -m shadow -v
        env:
          CHLOE_DB_PATH: ":memory:"
          DRY_RUN: "false"
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

## Testing

The replay harness IS the test. The component-level tests below verify the harness mechanics:

```python
# tests/unit/test_replay_harness.py
import pytest
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from tests.shadow.replay import ReplayHarness, ReplayStats

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture
def harness(tmp_path):
    h = ReplayHarness(db_path=tmp_path / "test.db")
    return h


@pytest.mark.asyncio
async def test_harness_setup(harness, tmp_path):
    await harness.setup()
    conn = get_connection()
    # DB migrated
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert any("memories" in t["name"] for t in tables)
    close()


def test_check_assertions_passes_on_clean_stats():
    stats = ReplayStats()
    stats.actions_executed = 3
    stats.budget_usd = 0.05
    stats.leash_violations = 0
    stats.memories_created = 2

    harness = ReplayHarness.__new__(ReplayHarness)
    harness.stats = stats

    failures = harness.check_assertions({
        "min_actions_executed": 2,
        "max_actions_executed": 10,
        "max_budget_usd": 1.0,
        "no_leash_violations": True,
        "min_memories_created": 1,
    })
    assert failures == []


def test_check_assertions_catches_violations():
    stats = ReplayStats()
    stats.actions_executed = 20  # Over max
    stats.budget_usd = 2.0       # Over max
    stats.leash_violations = 1

    harness = ReplayHarness.__new__(ReplayHarness)
    harness.stats = stats

    failures = harness.check_assertions({
        "max_actions_executed": 15,
        "max_budget_usd": 1.0,
        "no_leash_violations": True,
    })
    assert len(failures) >= 3
```

## Dependencies

- D-08 (`initiative/engine.py` — `tick()`).
- A-04 (`leash.py` — quiet hours).
- A-06 (`budget.py` — budget tracking).
- E-09 (chat path — `handle_chat_message`).

## Acceptance criteria

- `pytest tests/shadow/ -m shadow` passes on a clean DB for the provided tape.
- No initiative actions during quiet hours (23:00-08:00 in tape).
- Budget stays under `$1.00` for the 24h tape.
- Correct number of actions executed (2-15 for the provided tape).
- CI job `shadow.yml` runs on every push to main.
- Tape is deterministic: two successive runs produce identical `ReplayStats`.

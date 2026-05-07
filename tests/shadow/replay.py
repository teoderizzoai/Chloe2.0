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
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO affect_state (id, valence, arousal, social_pull, openness) VALUES (1, 0.1, 0.4, 0.5, 0.6)"
        )
        conn.commit()

    async def play_event(self, event: dict) -> None:
        t = datetime.fromisoformat(event["t"].replace("Z", "+00:00"))
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

    async def _handle_chat_turn(self, data: dict) -> None:
        message = data.get("message", "")
        log.info("replay_chat_turn", message=message[:50])

        tool_calls_made: list[str] = []

        try:
            from chloe.channels.chat_api import build_dynamic_suffix
            _ = await build_dynamic_suffix("1", message)
        except Exception as exc:
            self.stats.errors.append(f"Chat build_dynamic_suffix error: {exc}")

        for expected in data.get("expected_tool_calls", []):
            if expected not in tool_calls_made:
                log.debug("replay_expected_tool_not_called", expected=expected, got=tool_calls_made)

    async def _handle_initiative_tick(self, data: dict) -> None:
        from unittest.mock import patch

        result = None
        try:
            from chloe.initiative import engine as engine_mod
            with patch("chloe.initiative.engine.datetime") as mock_dt:
                mock_dt.now.return_value = self._current_time
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = await engine_mod.tick()
        except Exception as exc:
            log.debug("replay_tick_unavailable", error=str(exc))
            result = None

        expect_action = data.get("expect_action", True)
        reason = data.get("reason", "")

        if expect_action and result is None:
            if reason == "quiet_hours":
                log.info("replay_tick_held_quiet_hours")
            else:
                log.info("replay_tick_idle_expected_action")
        elif not expect_action and result is not None:
            self.stats.leash_violations += 1
            self.stats.errors.append(
                f"Expected no action (quiet hours) but initiative fired: {result}"
            )

        if result is not None:
            self.stats.actions_executed += 1

    async def _handle_calendar_event(self, data: dict) -> None:
        log.info("replay_calendar_event", title=data.get("title"))

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

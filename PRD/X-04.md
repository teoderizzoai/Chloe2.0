# X-04 · `tools/self_tools.py` — Chloe's self-modification tools

## Overview

`SelfToolsTool` with six verbs: `set_quiet(until)`, `set_focus(mode, until)`, `add_goal(...)`, `add_want(...)`, `update_preference(k, v)`, `archive_trait(traitId)`. Auth: `free`. Exposed to the chat model so Chloe can modify her own configuration mid-conversation — e.g., "I'll mute myself for a bit" or "I'm adding this to my goals".

## Context

These are the only tools that modify Chloe's internal state rather than the external world. They're `free` because Chloe should be able to self-regulate without requiring Teo's explicit confirmation for each introspective act. The `set_quiet` and `set_focus` tools map to the same preference keys that the leash reads, so changes take effect immediately. `add_goal` and `add_want` write to the identity tables that the weekly self-model reads.

**When:** Phase A (leash path), wired to chat model in Phase C.

## Implementation

### `tools/self_tools.py`

```python
# chloe/tools/self_tools.py
from __future__ import annotations
from datetime import datetime
from chloe.tools.base import BaseTool, ToolVerb, ToolResult
from chloe.state.db import get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.observability.logging import get_logger
import json
import ulid

log = get_logger("self_tools")


class SelfToolsTool(BaseTool):
    name = "self_tools"

    def __init__(self):
        self.verbs = {
            "set_quiet": ToolVerb(
                name="set_quiet",
                schema={
                    "type": "object",
                    "properties": {
                        "until": {"type": "string", "description": "ISO 8601 datetime or duration string like '2h', '30m'"},
                        "reason": {"type": "string", "description": "Optional reason for quiet mode"},
                    },
                    "required": ["until"],
                },
                auth_class="free",
                reversibility=1.0,
                description_for_model="Put Chloe into quiet mode until a given time. No proactive outreach until then.",
                description_for_human="Set quiet mode",
            ),
            "set_focus": ToolVerb(
                name="set_focus",
                schema={
                    "type": "object",
                    "properties": {
                        "mode": {"type": "boolean", "description": "True to enable focus mode"},
                        "until": {"type": "string", "description": "Optional: ISO 8601 or duration string"},
                    },
                    "required": ["mode"],
                },
                auth_class="free",
                reversibility=1.0,
                description_for_model="Enable or disable focus mode. When on, only urgent messages allowed.",
                description_for_human="Set focus mode",
            ),
            "add_goal": ToolVerb(
                name="add_goal",
                schema={
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string", "description": "Short identifier for the goal"},
                        "description": {"type": "string", "description": "Full goal description"},
                    },
                    "required": ["tag", "description"],
                },
                auth_class="free",
                reversibility=0.9,
                description_for_model="Add a new goal to Chloe's inner goal list.",
                description_for_human="Add inner goal",
            ),
            "add_want": ToolVerb(
                name="add_want",
                schema={
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Description of what Chloe wants"},
                    },
                    "required": ["description"],
                },
                auth_class="free",
                reversibility=0.9,
                description_for_model="Add a new want to Chloe's inner wants list.",
                description_for_human="Add inner want",
            ),
            "update_preference": ToolVerb(
                name="update_preference",
                schema={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Preference key to update"},
                        "value": {"description": "New value (any JSON-serializable type)"},
                    },
                    "required": ["key", "value"],
                },
                auth_class="free",
                reversibility=0.9,
                description_for_model="Update a preference setting. Use sparingly — only for self-regulation.",
                description_for_human="Update preference",
            ),
            "archive_trait": ToolVerb(
                name="archive_trait",
                schema={
                    "type": "object",
                    "properties": {
                        "trait_id": {"type": "string", "description": "ID of the identity trait to archive"},
                        "reason": {"type": "string", "description": "Why this trait is being archived"},
                    },
                    "required": ["trait_id"],
                },
                auth_class="free",
                reversibility=0.8,
                description_for_model="Archive an identity trait that no longer reflects who Chloe is.",
                description_for_human="Archive identity trait",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "set_quiet":
            return self._set_quiet(args)
        elif verb == "set_focus":
            return self._set_focus(args)
        elif verb == "add_goal":
            return self._add_goal(args)
        elif verb == "add_want":
            return self._add_want(args)
        elif verb == "update_preference":
            return self._update_preference(args)
        elif verb == "archive_trait":
            return self._archive_trait(args)
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    def _set_quiet(self, args: dict) -> ToolResult:
        until_str = args.get("until", "")
        until_dt = _parse_until(until_str)
        if until_dt is None:
            return ToolResult(success=False, error=f"Cannot parse 'until': {until_str!r}")

        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
            ("quiet_until", json.dumps(until_dt.isoformat())),
        )
        conn.commit()
        log.info("self_quiet_set", until=until_dt.isoformat(), reason=args.get("reason"))
        return ToolResult(success=True, data={"quiet_until": until_dt.isoformat()})

    def _set_focus(self, args: dict) -> ToolResult:
        mode = bool(args.get("mode", True))
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
            ("focus_mode", json.dumps(mode)),
        )
        conn.commit()

        if args.get("until"):
            until_dt = _parse_until(args["until"])
            if until_dt:
                conn.execute(
                    "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
                    ("focus_until", json.dumps(until_dt.isoformat())),
                )
                conn.commit()

        log.info("self_focus_set", mode=mode)
        return ToolResult(success=True, data={"focus_mode": mode})

    def _add_goal(self, args: dict) -> ToolResult:
        goal_id = str(ulid.new())
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO inner_goals (id, tag, description, progress, source, created_at)
            VALUES (?, ?, ?, 0.0, 'self_tools', ?)
            """,
            (goal_id, args.get("tag", ""), args.get("description", ""), datetime.utcnow().isoformat()),
        )
        conn.commit()
        log.info("self_goal_added", goal_id=goal_id, tag=args.get("tag"))
        return ToolResult(success=True, data={"goal_id": goal_id})

    def _add_want(self, args: dict) -> ToolResult:
        want_id = str(ulid.new())
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO inner_wants (id, description, source, created_at)
            VALUES (?, ?, 'self_tools', ?)
            """,
            (want_id, args.get("description", ""), datetime.utcnow().isoformat()),
        )
        conn.commit()
        log.info("self_want_added", want_id=want_id)
        return ToolResult(success=True, data={"want_id": want_id})

    def _update_preference(self, args: dict) -> ToolResult:
        key = args.get("key", "")
        value = args.get("value")

        # Blocklist: these keys cannot be changed via self_tools
        BLOCKED_KEYS = {"ha_blocklist", "ha_allowlist", "gmail_dont_send_to"}
        if key in BLOCKED_KEYS:
            return ToolResult(success=False, error=f"Preference {key!r} cannot be modified via self_tools")

        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        conn.commit()
        log.info("self_preference_updated", key=key)
        return ToolResult(success=True, data={"key": key, "value": value})

    def _archive_trait(self, args: dict) -> ToolResult:
        trait_id = args.get("trait_id", "")
        conn = get_connection()
        result = conn.execute(
            "UPDATE identity_traits SET archived=1, archive_reason=? WHERE id=?",
            (args.get("reason", ""), trait_id),
        )
        conn.commit()
        if result.rowcount == 0:
            return ToolResult(success=False, error=f"Trait {trait_id!r} not found")
        log.info("self_trait_archived", trait_id=trait_id)
        return ToolResult(success=True, data={"trait_id": trait_id, "archived": True})

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "set_quiet":
            return f"Would set quiet mode until {args.get('until', '?')}"
        elif verb == "set_focus":
            return f"Would {'enable' if args.get('mode') else 'disable'} focus mode"
        elif verb == "add_goal":
            return f"Would add goal: {args.get('tag', '?')} — {args.get('description', '?')[:50]}"
        elif verb == "add_want":
            return f"Would add want: {args.get('description', '?')[:50]}"
        elif verb == "update_preference":
            return f"Would set preference {args.get('key', '?')} = {args.get('value', '?')!r}"
        elif verb == "archive_trait":
            return f"Would archive trait {args.get('trait_id', '?')}"
        return super().dry_run(verb, args)


def _parse_until(until_str: str) -> datetime | None:
    """Parse ISO 8601 or duration strings like '2h', '30m', '1d'."""
    from datetime import timedelta
    if not until_str:
        return None

    # Try ISO 8601 first
    try:
        return datetime.fromisoformat(until_str)
    except ValueError:
        pass

    # Duration strings
    s = until_str.strip().lower()
    now = datetime.utcnow()
    try:
        if s.endswith("h"):
            return now + timedelta(hours=float(s[:-1]))
        elif s.endswith("m"):
            return now + timedelta(minutes=float(s[:-1]))
        elif s.endswith("d"):
            return now + timedelta(days=float(s[:-1]))
    except ValueError:
        pass

    return None
```

### Migration — add `archived` column to `identity_traits`

```sql
-- In chloe/state/migrations/0007_trait_archive.sql
ALTER TABLE identity_traits ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
ALTER TABLE identity_traits ADD COLUMN archive_reason TEXT NOT NULL DEFAULT '';
```

### Register in ToolRegistry

```python
from chloe.tools.self_tools import SelfToolsTool
self._tools["self_tools"] = SelfToolsTool()
```

## Testing

### Unit tests — `tests/unit/test_self_tools.py`

```python
import pytest
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.tools.self_tools import SelfToolsTool, _parse_until
from datetime import datetime, timedelta

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def tool():
    return SelfToolsTool()


def test_all_verbs_are_free(tool):
    for verb in tool.verbs.values():
        assert verb.auth_class == "free", f"{verb.name} is not free"


def test_parse_until_iso():
    dt = _parse_until("2026-05-10T10:00:00")
    assert dt == datetime(2026, 5, 10, 10, 0, 0)


def test_parse_until_duration():
    before = datetime.utcnow()
    dt = _parse_until("2h")
    assert dt is not None
    diff = dt - before
    assert 7100 < diff.total_seconds() < 7300  # ~2 hours


def test_parse_until_minutes():
    dt = _parse_until("30m")
    assert dt is not None


def test_parse_until_invalid():
    assert _parse_until("garbage") is None
    assert _parse_until("") is None


@pytest.mark.asyncio
async def test_set_quiet_writes_preference(tool, db):
    result = await tool.execute("set_quiet", {"until": "2h"})
    assert result.success
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='quiet_until'").fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_set_focus_writes_preference(tool, db):
    result = await tool.execute("set_focus", {"mode": True})
    assert result.success
    import json
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='focus_mode'").fetchone()
    assert json.loads(row["value"]) is True


@pytest.mark.asyncio
async def test_add_goal_creates_row(tool, db):
    result = await tool.execute("add_goal", {"tag": "learn_python", "description": "Master Python async"})
    assert result.success
    goal_id = result.data["goal_id"]
    conn = get_connection()
    row = conn.execute("SELECT * FROM inner_goals WHERE id=?", (goal_id,)).fetchone()
    assert row["tag"] == "learn_python"
    assert row["source"] == "self_tools"


@pytest.mark.asyncio
async def test_add_want_creates_row(tool, db):
    result = await tool.execute("add_want", {"description": "Understand music theory better"})
    assert result.success
    want_id = result.data["want_id"]
    conn = get_connection()
    row = conn.execute("SELECT * FROM inner_wants WHERE id=?", (want_id,)).fetchone()
    assert "music theory" in row["description"]


@pytest.mark.asyncio
async def test_update_preference_blocked_keys(tool, db):
    for key in ["ha_blocklist", "ha_allowlist", "gmail_dont_send_to"]:
        result = await tool.execute("update_preference", {"key": key, "value": []})
        assert not result.success
        assert "cannot be modified" in result.error


@pytest.mark.asyncio
async def test_update_preference_allowed_key(tool, db):
    result = await tool.execute("update_preference", {"key": "custom_note", "value": "hello"})
    assert result.success


@pytest.mark.asyncio
async def test_archive_trait_nonexistent(tool, db):
    result = await tool.execute("archive_trait", {"trait_id": "nonexistent_id"})
    assert not result.success
    assert "not found" in result.error
```

## Dependencies

- F-04 (`inner_goals`, `inner_wants`, `identity_traits`, `preferences` tables).
- A-04 (`leash.py` — reads `quiet_until`, `focus_mode` from preferences).
- `0007_trait_archive.sql` migration.

## Acceptance criteria

- All 6 verbs have `auth_class="free"`.
- `set_quiet("2h")` → `preferences["quiet_until"]` set to 2h from now.
- `set_focus(True)` → `preferences["focus_mode"]` = `true`.
- `add_goal` → `inner_goals` row created with `source="self_tools"`.
- `add_want` → `inner_wants` row created.
- `update_preference` blocked for safety keys (`ha_blocklist`, etc.).
- `archive_trait` on nonexistent ID → error without exception.
- Duration strings (`"2h"`, `"30m"`, `"1d"`) parsed correctly.

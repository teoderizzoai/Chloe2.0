from __future__ import annotations

import json
from datetime import datetime, timedelta

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("self_tools")


class SelfToolsTool(Tool):
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
                description_for_model="Put Chloe into quiet mode until a given time.",
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
                description_for_model="Enable or disable focus mode.",
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
                description_for_model="Update a preference setting.",
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
        conn = get_connection()
        cursor = conn.execute(
            """
            INSERT INTO inner_goals (name, why, created_at)
            VALUES (?, ?, datetime('now'))
            """,
            (args.get("tag", ""), args.get("description", "")),
        )
        conn.commit()
        goal_id = cursor.lastrowid
        log.info("self_goal_added", goal_id=goal_id, tag=args.get("tag"))
        return ToolResult(success=True, data={"goal_id": goal_id})

    def _add_want(self, args: dict) -> ToolResult:
        conn = get_connection()
        cursor = conn.execute(
            """
            INSERT INTO inner_wants (text, created_at)
            VALUES (?, datetime('now'))
            """,
            (args.get("description", ""),),
        )
        conn.commit()
        want_id = cursor.lastrowid
        log.info("self_want_added", want_id=want_id)
        return ToolResult(success=True, data={"want_id": want_id})

    def _update_preference(self, args: dict) -> ToolResult:
        key = args.get("key", "")
        value = args.get("value")

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
            "UPDATE identity_traits SET archived=1, archive_reason=?, status='archived' WHERE id=?",
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
    if not until_str:
        return None

    try:
        return datetime.fromisoformat(until_str)
    except ValueError:
        pass

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

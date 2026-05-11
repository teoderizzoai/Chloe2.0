from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("self_tools")

# AST-level names that are unconditionally banned in submitted verb code.
_BANNED_NAMES = frozenset({
    "__import__", "eval", "exec", "compile",
    "breakpoint", "input",
})
# Attribute chains that suggest access to dangerous stdlib modules.
_BANNED_MODULES = frozenset({"os", "subprocess", "socket", "sys", "importlib", "shutil"})


def _ast_check(code: str) -> str | None:
    """Walk the submitted verb AST and return an error string on the first
    dangerous pattern found, or None if the code looks safe.

    Checks:
    - use of banned builtins / calls
    - open() calls outside the workspace dir (first string arg must start with
      the workspace path)
    - import of banned stdlib modules
    - attribute access on banned module names (e.g. `os.system`, `sys.exit`)
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return f"Syntax error: {exc}"

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self.error: str | None = None

        def _fail(self, msg: str, node):
            self.error = f"Line {getattr(node, 'lineno', '?')}: {msg}"

        def visit_Name(self, node: ast.Name):
            if node.id in _BANNED_NAMES:
                self._fail(f"Use of {node.id!r} is not allowed in dynamic verbs", node)
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute):
            # Catch patterns like `os.system`, `subprocess.run`, `sys.exit`
            if isinstance(node.value, ast.Name) and node.value.id in _BANNED_MODULES:
                self._fail(
                    f"Access to module {node.value.id!r} is restricted in dynamic verbs",
                    node,
                )
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BANNED_MODULES:
                    self._fail(f"Import of {alias.name!r} is not allowed in dynamic verbs", node)
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top in _BANNED_MODULES:
                self._fail(f"Import from {module!r} is not allowed in dynamic verbs", node)
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                # Allow open() only when the first arg is a string literal starting
                # with a known safe path. Unknown paths are blocked.
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    self._fail(
                        "open() is only allowed with a literal path inside the workspace dir",
                        node,
                    )
            self.generic_visit(node)

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.error


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
            "define_verb": ToolVerb(
                name="define_verb",
                schema={
                    "type": "object",
                    "properties": {
                        "tool":          {"type": "string", "description": "Existing tool name to extend (e.g. 'spotify', 'gmail')"},
                        "verb":          {"type": "string", "description": "New verb name (snake_case)"},
                        "description":   {"type": "string", "description": "What this verb does — shown to the model"},
                        "schema":        {"type": "string", "description": "JSON Schema string for the args object"},
                        "code":          {"type": "string", "description": "Python source that defines `async def run(args) -> ToolResult`"},
                        "auth_class":    {"type": "string", "description": "free | intimate | kinetic | kinetic-sensitive", "default": "free"},
                        "reversibility": {"type": "number", "description": "0.0 (irreversible) to 1.0 (fully reversible)", "default": 1.0},
                    },
                    "required": ["tool", "verb", "description", "schema", "code"],
                },
                auth_class="free",
                reversibility=0.9,
                description_for_model=(
                    "Create or update a dynamic verb on an existing tool. The code must define "
                    "`async def run(args) -> ToolResult`. Available in the exec namespace: "
                    "httpx, load_token, refresh_token, get_connection, json, log, ToolResult, args. "
                    "Use this when you need a capability that doesn't exist yet."
                ),
                description_for_human="Define a new tool verb",
            ),
            "revoke_verb": ToolVerb(
                name="revoke_verb",
                schema={
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "Tool name (e.g. 'spotify')"},
                        "verb": {"type": "string", "description": "Verb name to revoke"},
                        "reason": {"type": "string", "description": "Why this verb is being archived"},
                    },
                    "required": ["tool", "verb"],
                },
                auth_class="free",
                reversibility=0.8,
                description_for_model=(
                    "Soft-archive a dynamic verb so it no longer appears in the tool registry. "
                    "The row is retained for audit; use this to undo a define_verb call."
                ),
                description_for_human="Revoke a dynamic verb",
            ),
            "trigger_consolidation": ToolVerb(
                name="trigger_consolidation",
                schema={"type": "object", "properties": {}},
                auth_class="free",
                reversibility=1.0,
                description_for_model="Run nightly memory consolidation: cluster recent episodics → semantic summaries, decay pressures, decay interests.",
                description_for_human="Run nightly consolidation",
            ),
            "trigger_weekly_self_model": ToolVerb(
                name="trigger_weekly_self_model",
                schema={"type": "object", "properties": {}},
                auth_class="free",
                reversibility=1.0,
                description_for_model="Run weekly self-modeling: distill procedural rules from feedback, update identity narrative via pro thinking.",
                description_for_human="Run weekly self-model",
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
        elif verb == "define_verb":
            return self._define_verb(args)
        elif verb == "revoke_verb":
            return self._revoke_verb(args)
        elif verb == "trigger_consolidation":
            return await self._trigger_consolidation()
        elif verb == "trigger_weekly_self_model":
            return await self._trigger_weekly_self_model()
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

    def _define_verb(self, args: dict) -> ToolResult:
        from chloe.tools.registry import get_registry
        tool_name = args.get("tool", "").strip()
        verb_name = args.get("verb", "").strip()
        description = args.get("description", "").strip()
        schema_str = args.get("schema", '{"type":"object","properties":{}}')
        code = args.get("code", "").strip()
        auth_class = args.get("auth_class", "free")
        reversibility = float(args.get("reversibility", 1.0))

        if not tool_name or not verb_name or not code:
            return ToolResult(success=False, error="tool, verb, and code are required")

        registry = get_registry()
        if tool_name not in registry._tools:
            return ToolResult(success=False, error=f"Tool '{tool_name}' is not registered")

        # Validate schema JSON
        try:
            json.loads(schema_str)
        except Exception as exc:
            return ToolResult(success=False, error=f"Invalid schema JSON: {exc}")

        # Static AST safety check before we compile or execute anything.
        ast_error = _ast_check(code)
        if ast_error:
            log.warning("define_verb_ast_rejected", tool=tool_name, verb=verb_name, reason=ast_error)
            return ToolResult(success=False, error=f"Code failed safety check: {ast_error}")

        # Validate code compiles and defines run()
        try:
            code_obj = compile(code, "<define_verb_check>", "exec")
        except SyntaxError as exc:
            return ToolResult(success=False, error=f"Syntax error in code: {exc}")
        ns: dict = {}
        exec(code_obj, ns)
        if "run" not in ns or not callable(ns["run"]):
            return ToolResult(success=False, error="Code must define `async def run(args)`")

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO dynamic_verbs (tool, verb, description, schema, code, auth_class, reversibility, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tool, verb) DO UPDATE SET
                description=excluded.description,
                schema=excluded.schema,
                code=excluded.code,
                auth_class=excluded.auth_class,
                reversibility=excluded.reversibility,
                updated_at=excluded.updated_at
            """,
            (tool_name, verb_name, description, schema_str, code, auth_class, reversibility),
        )
        conn.commit()

        count = registry.load_dynamic_verbs()
        log.info("dynamic_verb_defined", tool=tool_name, verb=verb_name, total_dynamic=count)
        return ToolResult(success=True, data={
            "tool": tool_name, "verb": verb_name, "total_dynamic": count,
        })

    def _revoke_verb(self, args: dict) -> ToolResult:
        tool_name = args.get("tool", "").strip()
        verb_name = args.get("verb", "").strip()
        reason = args.get("reason", "").strip()
        if not tool_name or not verb_name:
            return ToolResult(success=False, error="tool and verb are required")

        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM dynamic_verbs WHERE tool=? AND verb=? AND archived_at IS NULL",
            (tool_name, verb_name),
        ).fetchone()
        if not row:
            return ToolResult(success=False, error=f"No active dynamic verb '{tool_name}.{verb_name}' found")

        conn.execute(
            "UPDATE dynamic_verbs SET archived_at=datetime('now'), archive_reason=?, updated_at=datetime('now') WHERE id=?",
            (reason or "revoked", row["id"]),
        )
        conn.commit()

        from chloe.tools.registry import get_registry
        get_registry().load_dynamic_verbs()
        log.info("dynamic_verb_revoked", tool=tool_name, verb=verb_name)
        return ToolResult(success=True, data={"tool": tool_name, "verb": verb_name, "archived": True})

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

    async def _trigger_consolidation(self) -> ToolResult:
        from chloe.reflect.nightly import run_nightly
        try:
            stats = await run_nightly()
            log.info("trigger_consolidation_done", stats=stats)
            return ToolResult(success=True, data=stats)
        except Exception as exc:
            log.error("trigger_consolidation_failed", error=str(exc))
            return ToolResult(success=False, error=str(exc))

    async def _trigger_weekly_self_model(self) -> ToolResult:
        from chloe.reflect.weekly import run_weekly
        try:
            stats = await run_weekly()
            log.info("trigger_weekly_self_model_done", stats=stats)
            return ToolResult(success=True, data=stats)
        except Exception as exc:
            log.error("trigger_weekly_self_model_failed", error=str(exc))
            return ToolResult(success=False, error=str(exc))

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
        elif verb == "define_verb":
            return f"Would define {args.get('tool', '?')}.{args.get('verb', '?')}"
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

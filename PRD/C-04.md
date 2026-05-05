# C-04 · `tools/reminders.py` — reminders tool

## Overview

Implement `chloe/tools/reminders.py` with verbs `add(title, due?)`, `complete(taskId)`, `list()`. Auth: `kinetic`. Backed by Google Tasks (via Google API) using the stored Google OAuth token. Dry-run works without an actual backend.

## Context

Reminders let Chloe set timely reminders for Teo — "I set a reminder for your 9am dentist appointment" is a useful real-world action. Google Tasks is chosen as the default backend because the Google OAuth token is already stored from B-03. Apple Reminders (EventKit) requires a macOS helper process.

## Google Tasks API

- `POST /tasks/v1/lists/@default/tasks` — create a task.
- `PATCH /tasks/v1/lists/@default/tasks/{taskId}` — update (mark complete).
- `GET /tasks/v1/lists/@default/tasks` — list tasks.

## Implementation

```python
# chloe/tools/reminders.py

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.state.oauth_tokens import load as load_token, refresh as refresh_token
from chloe.observability.logging import get_logger
import httpx

log = get_logger("tool.reminders")
TASKS_API = "https://tasks.googleapis.com/tasks/v1"

class RemindersTool(Tool):
    name = "reminders"

    def __init__(self):
        self.verbs = {
            "add": ToolVerb(
                name="add",
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "due": {"type": "string", "description": "ISO 8601 datetime (optional)"},
                    },
                    "required": ["title"],
                },
                auth_class="kinetic", reversibility=0.9,
                description_for_model="Add a reminder to Teo's Google Tasks list.",
                description_for_human="Add reminder",
            ),
            "complete": ToolVerb(
                name="complete",
                schema={"type":"object","properties":{"taskId":{"type":"string"}},"required":["taskId"]},
                auth_class="kinetic", reversibility=0.4,
                description_for_model="Mark a reminder as complete.",
                description_for_human="Complete reminder",
            ),
            "list": ToolVerb(
                name="list",
                schema={"type":"object","properties":{}},
                auth_class="intimate", reversibility=1.0,
                description_for_model="List Teo's current reminders.",
                description_for_human="List reminders",
            ),
        }

    async def _headers(self) -> dict | None:
        token = load_token("google")
        if not token:
            return None
        return {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"}

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "add":
            due_str = f" due {args['due']}" if args.get("due") else ""
            return f"Would add reminder: '{args.get('title', '?')}'{due_str}"
        if verb == "complete":
            return f"Would mark task {args.get('taskId', '?')} as complete"
        return super().dry_run(verb, args)

    async def execute(self, verb: str, args: dict) -> ToolResult:
        hdrs = await self._headers()
        if not hdrs:
            return ToolResult(success=False, error="No Google token — run OAuth flow first")

        if verb == "add":
            body = {"title": args["title"]}
            if args.get("due"):
                # RFC 3339 format for due date
                body["due"] = args["due"] if "T" in args["due"] else args["due"] + "T00:00:00Z"
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{TASKS_API}/lists/@default/tasks",
                    headers=hdrs,
                    json=body,
                )
            
            if resp.status_code == 401:
                new_token = await refresh_token("google")
                if new_token:
                    hdrs = {"Authorization": f"Bearer {new_token['access_token']}", "Content-Type": "application/json"}
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(f"{TASKS_API}/lists/@default/tasks", headers=hdrs, json=body)
            
            if resp.status_code in (200, 201):
                task = resp.json()
                return ToolResult(success=True, data={"taskId": task.get("id"), "title": task.get("title")})
            return ToolResult(success=False, error=f"Tasks API error: {resp.status_code}")

        elif verb == "complete":
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    f"{TASKS_API}/lists/@default/tasks/{args['taskId']}",
                    headers=hdrs,
                    json={"status": "completed"},
                )
            if resp.status_code in (200, 204):
                return ToolResult(success=True, data={"completed": args["taskId"]})
            return ToolResult(success=False, error=f"Tasks API error: {resp.status_code}")

        elif verb == "list":
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{TASKS_API}/lists/@default/tasks", headers=hdrs,
                                        params={"showCompleted": "false"})
            if resp.status_code != 200:
                return ToolResult(success=False, error=f"Tasks API error: {resp.status_code}")
            items = [{"id": t["id"], "title": t["title"], "due": t.get("due")}
                     for t in resp.json().get("items", [])]
            return ToolResult(success=True, data={"tasks": items})

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
```

## Dependencies

- A-06 (Tool base).
- B-01 (Google OAuth token).
- B-03 (Google OAuth scopes — `tasks` scope must be added if not already present).

Note: add `https://www.googleapis.com/auth/tasks` to the `GOOGLE_SCOPES` list in B-03.

## Testing

### Unit tests — `tests/unit/test_reminders.py`

```python
import pytest
from chloe.tools.reminders import RemindersTool

@pytest.fixture
def tool():
    return RemindersTool()

def test_dry_run_add(tool):
    preview = tool.dry_run("add", {"title": "Call dentist", "due": "2026-05-05"})
    assert "Call dentist" in preview
    assert "2026-05-05" in preview

def test_dry_run_add_no_due(tool):
    preview = tool.dry_run("add", {"title": "Buy milk"})
    assert "Buy milk" in preview

def test_all_verbs_defined(tool):
    assert "add" in tool.verbs
    assert "complete" in tool.verbs
    assert "list" in tool.verbs

def test_add_is_kinetic(tool):
    assert tool.verbs["add"].auth_class == "kinetic"

def test_list_is_intimate(tool):
    assert tool.verbs["list"].auth_class == "intimate"

@pytest.mark.asyncio
async def test_no_token_returns_error(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.reminders.load_token", lambda s: None)
    result = await tool.execute("add", {"title": "Test"})
    assert not result.success
    assert "token" in result.error.lower() or "OAuth" in result.error
```

### Integration test (`@pytest.mark.live`)

```python
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_add_reminder():
    tool = RemindersTool()
    result = await tool.execute("add", {"title": "Chloe test reminder — delete me"})
    assert result.success
    assert result.data.get("taskId")
```

## Acceptance criteria

- Dry-run works without a backend (returns preview string).
- Integration test (`@pytest.mark.live`) creates a real Google Task.
- `add` is `kinetic`, `list` is `intimate`.
- 401 triggers token refresh.

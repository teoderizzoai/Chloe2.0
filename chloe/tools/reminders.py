import httpx

from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import load as load_token
from chloe.state.oauth_tokens import refresh as refresh_token
from chloe.tools.base import Tool, ToolResult, ToolVerb

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
                auth_class="kinetic",
                reversibility=0.9,
                description_for_model="Add a reminder to Teo's Google Tasks list.",
                description_for_human="Add reminder",
            ),
            "complete": ToolVerb(
                name="complete",
                schema={"type": "object", "properties": {"taskId": {"type": "string"}}, "required": ["taskId"]},
                auth_class="kinetic",
                reversibility=0.4,
                description_for_model="Mark a reminder as complete.",
                description_for_human="Complete reminder",
            ),
            "list": ToolVerb(
                name="list",
                schema={"type": "object", "properties": {}},
                auth_class="intimate",
                reversibility=1.0,
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
        if verb == "list":
            return "Would list reminders"
        return super().dry_run(verb, args)

    async def execute(self, verb: str, args: dict) -> ToolResult:
        hdrs = await self._headers()
        if not hdrs:
            return ToolResult(success=False, error="No Google token — run OAuth flow first")

        if verb == "add":
            body: dict = {"title": args["title"]}
            if args.get("due"):
                body["due"] = args["due"] if "T" in args["due"] else args["due"] + "T00:00:00Z"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{TASKS_API}/lists/@default/tasks", headers=hdrs, json=body)
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
                resp = await client.get(
                    f"{TASKS_API}/lists/@default/tasks",
                    headers=hdrs,
                    params={"showCompleted": "false"},
                )
            if resp.status_code != 200:
                return ToolResult(success=False, error=f"Tasks API error: {resp.status_code}")
            items = [
                {"id": t["id"], "title": t["title"], "due": t.get("due")}
                for t in resp.json().get("items", [])
            ]
            return ToolResult(success=True, data={"tasks": items})

        return ToolResult(success=False, error=f"Unknown verb: {verb}")

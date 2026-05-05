import base64

import httpx

from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import load as load_token
from chloe.state.oauth_tokens import refresh as refresh_token
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.gmail")
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class GmailTool(Tool):
    name = "gmail"

    def __init__(self):
        self.verbs = {
            "read_recent": ToolVerb(
                name="read_recent",
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 5},
                        "label": {"type": "string"},
                    },
                },
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Read recent emails from Teo's Gmail inbox.",
                description_for_human="Read recent Gmail",
            ),
            "read_thread": ToolVerb(
                name="read_thread",
                schema={"type": "object", "properties": {"threadId": {"type": "string"}}, "required": ["threadId"]},
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Read a specific Gmail thread by ID.",
                description_for_human="Read Gmail thread",
            ),
            "search": ToolVerb(
                name="search",
                schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Search Teo's Gmail with a query string.",
                description_for_human="Search Gmail",
            ),
            "summarize_inbox": ToolVerb(
                name="summarize_inbox",
                schema={"type": "object", "properties": {"window": {"type": "string", "default": "24h"}}},
                auth_class="intimate",
                reversibility=1.0,
                cost_per_call_usd=0.001,
                description_for_model="Summarize Teo's inbox activity in the last N hours using a Flash call.",
                description_for_human="Summarize inbox",
            ),
        }

    async def _headers(self) -> dict | None:
        token = load_token("google")
        if not token:
            return None
        return {"Authorization": f"Bearer {token['access_token']}"}

    async def _get(self, path: str, params: dict | None = None) -> dict | None:
        hdrs = await self._headers()
        if not hdrs:
            return None
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GMAIL_API}{path}", headers=hdrs, params=params)
            if resp.status_code == 401:
                new_token = await refresh_token("google")
                if new_token:
                    hdrs = {"Authorization": f"Bearer {new_token['access_token']}"}
                    resp = await client.get(f"{GMAIL_API}{path}", headers=hdrs, params=params)
            if resp.status_code != 200:
                log.error("gmail_api_error", path=path, status=resp.status_code)
                return None
            return resp.json()

    def _decode_body(self, payload: dict) -> str:
        def extract(part):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            for p in part.get("parts", []):
                result = extract(p)
                if result:
                    return result
            return ""
        return extract(payload)[:2000]

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "read_recent":
            limit = min(args.get("limit", 5), 20)
            label = args.get("label", "INBOX")
            data = await self._get("/users/me/messages", params={"maxResults": limit, "labelIds": label})
            if not data:
                return ToolResult(success=False, error="Failed to read Gmail")
            messages = []
            for msg in data.get("messages", []):
                msg_data = await self._get(f"/users/me/messages/{msg['id']}", params={"format": "metadata"})
                if msg_data:
                    headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                    messages.append({
                        "id": msg["id"],
                        "threadId": msg_data.get("threadId"),
                        "subject": headers.get("Subject", ""),
                        "from": headers.get("From", ""),
                        "date": headers.get("Date", ""),
                        "snippet": msg_data.get("snippet", ""),
                    })
            return ToolResult(success=True, data={"messages": messages})

        if verb == "read_thread":
            data = await self._get(f"/users/me/threads/{args['threadId']}", params={"format": "full"})
            if not data:
                return ToolResult(success=False, error="Thread not found")
            messages = []
            for msg in data.get("messages", []):
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                messages.append({
                    "id": msg["id"],
                    "from": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "body": self._decode_body(msg.get("payload", {})),
                })
            return ToolResult(success=True, data={"threadId": args["threadId"], "messages": messages})

        if verb == "search":
            data = await self._get("/users/me/messages", params={"q": args["query"], "maxResults": 10})
            if not data:
                return ToolResult(success=True, data={"messages": []})
            return ToolResult(success=True, data={"messages": data.get("messages", [])})

        if verb == "summarize_inbox":
            recent_result = await self.execute("read_recent", {"limit": 10})
            if not recent_result.success:
                return recent_result
            messages = recent_result.data.get("messages", [])
            try:
                from chloe.config import get_settings
                from chloe.llm.gemini import GeminiClient
                s = get_settings()
                client = GeminiClient(api_key=getattr(s, "gemini_api_key", None))
                summary = await client.flash("synthesize_cluster.md", {
                    "messages": messages,
                    "window": args.get("window", "24h"),
                    "task": "summarize inbox",
                }, None)
                return ToolResult(success=True, data={"summary": summary or "", "unread_count": len(messages)})
            except Exception as e:
                return ToolResult(success=False, error=f"Summarization failed: {e}")

        return ToolResult(success=False, error=f"Unknown verb: {verb}")

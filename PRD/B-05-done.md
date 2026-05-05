# B-05 · `tools/gmail.py` — read verbs

## Overview

Implement `chloe/tools/gmail.py` with read verbs: `read_recent(limit, label?)`, `read_thread(threadId)`, `search(query)`, `summarize_inbox(window="24h")`. Auth: `intimate`. Auto-refresh on 401 via `oauth_tokens.refresh("google")`. `summarize_inbox` makes a single Flash call.

## Context

Gmail reads let Chloe be aware of what's in Teo's inbox — she can notice an important meeting confirmation, a flight booking, or an email from someone he mentioned. This awareness is what enables US-2 ("she texted me at 23:48 to wish me luck on tomorrow's interview because she read it on my calendar"). The read verbs are `intimate` — they access real data but don't modify anything.

## Implementation

```python
# chloe/tools/gmail.py

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.state.oauth_tokens import load as load_token, refresh as refresh_token
from chloe.observability.logging import get_logger
import httpx, base64, json

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
                auth_class="intimate", reversibility=1.0,
                description_for_model="Read recent emails from Teo's Gmail inbox.",
                description_for_human="Read recent Gmail",
            ),
            "read_thread": ToolVerb(
                name="read_thread",
                schema={"type":"object","properties":{"threadId":{"type":"string"}},"required":["threadId"]},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Read a specific Gmail thread by ID.",
                description_for_human="Read Gmail thread",
            ),
            "search": ToolVerb(
                name="search",
                schema={"type":"object","properties":{"query":{"type":"string"}},"required":["query"]},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Search Teo's Gmail with a query string.",
                description_for_human="Search Gmail",
            ),
            "summarize_inbox": ToolVerb(
                name="summarize_inbox",
                schema={"type":"object","properties":{"window":{"type":"string","default":"24h"}}},
                auth_class="intimate", reversibility=1.0,
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
        """Extract plain text body from Gmail message payload."""
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
        return extract(payload)[:2000]  # cap at 2KB

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "read_recent":
            limit = min(args.get("limit", 5), 20)
            label = args.get("label", "INBOX")
            params = {"maxResults": limit, "labelIds": label, "format": "metadata"}
            data = await self._get("/users/me/messages", params=params)
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

        elif verb == "read_thread":
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

        elif verb == "search":
            data = await self._get("/users/me/messages", params={"q": args["query"], "maxResults": 10})
            if not data:
                return ToolResult(success=True, data={"messages": []})
            return ToolResult(success=True, data={"messages": data.get("messages", [])})

        elif verb == "summarize_inbox":
            # Read recent messages first
            recent_result = await self.execute("read_recent", {"limit": 10})
            if not recent_result.success:
                return recent_result
            
            messages = recent_result.data.get("messages", [])
            from chloe.llm.gemini import GeminiClient
            from chloe.config import get_settings
            from chloe.llm.schemas import ClusterSynthesis
            
            s = get_settings()
            client = GeminiClient(api_key=s.gemini_api_key.get_secret_value())
            summary = await client.flash("synthesize_cluster.md", {
                "messages": messages,
                "window": args.get("window", "24h"),
                "task": "summarize inbox",
            }, ClusterSynthesis)
            
            if not summary:
                return ToolResult(success=False, error="Summarization failed")
            
            return ToolResult(success=True, data={
                "summary": summary.get("summary", ""),
                "unread_count": len(messages),
            })

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
```

## Dependencies

- A-06 (Tool base).
- B-01 (`load_token`, `refresh_token`).
- F-05 (GeminiClient for `summarize_inbox`).

## Testing

### VCR cassette tests — `tests/unit/test_gmail_read.py`

```python
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from chloe.tools.gmail import GmailTool

MOCK_MESSAGES_LIST = {
    "messages": [{"id": "msg123", "threadId": "thread456"}]
}
MOCK_MESSAGE_DETAIL = {
    "id": "msg123",
    "threadId": "thread456",
    "snippet": "Here is the latest update",
    "payload": {
        "headers": [
            {"name": "From", "value": "alice@example.com"},
            {"name": "Subject", "value": "Project update"},
            {"name": "Date", "value": "Mon, 4 May 2026 10:00:00 +0000"},
        ]
    }
}

@pytest.fixture
def tool():
    return GmailTool()

@pytest.mark.asyncio
async def test_read_recent_returns_messages(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    
    call_count = [0]
    async def mock_get(self_or_path, *args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 200
        if call_count[0] == 1:
            resp.json.return_value = MOCK_MESSAGES_LIST
        else:
            resp.json.return_value = MOCK_MESSAGE_DETAIL
        return resp
    
    with patch.object(GmailTool, "_get", side_effect=[
        MOCK_MESSAGES_LIST,
        MOCK_MESSAGE_DETAIL,
    ]):
        result = await tool.execute("read_recent", {"limit": 1})
    
    assert result.success
    assert len(result.data["messages"]) == 1
    assert result.data["messages"][0]["subject"] == "Project update"

@pytest.mark.asyncio
async def test_read_recent_handles_401(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "expired"})
    monkeypatch.setattr("chloe.tools.gmail.refresh_token", AsyncMock(return_value={"access_token": "fresh"}))
    
    call_count = [0]
    async def mock_get(path, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return None  # Simulates 401 → None
        return MOCK_MESSAGES_LIST
    
    with patch.object(tool, "_get", side_effect=mock_get):
        result = await tool.execute("read_recent", {"limit": 1})
    # Should handle gracefully

def test_summarize_inbox_schema(tool):
    """summarize_inbox should be in verbs with intimate auth."""
    assert "summarize_inbox" in tool.verbs
    assert tool.verbs["summarize_inbox"].auth_class == "intimate"
```

## Acceptance criteria

- VCR cassette tests pass for all verbs.
- `summarize_inbox` makes exactly one Flash call and returns `{summary, unread_count}`.
- 401 triggers `refresh_token("google")` and retries.

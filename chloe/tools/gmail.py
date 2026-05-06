import base64
import email.mime.text
import json

import httpx

from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import load as load_token
from chloe.state.oauth_tokens import refresh as refresh_token
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.gmail")
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
GMAIL_FOOTER = "\n\n— sent with help from Chloe"


def _register_artifact(kind: str, ref: str, title: str, action_id: str) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO artifact_index (kind, ref, title, created_by_action, exists_) VALUES (?, ?, ?, ?, 1)",
        (kind, ref, title, action_id),
    )
    conn.commit()


def _build_draft_body(body: str) -> str:
    from chloe.state.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='gmail_footer'").fetchone()
    footer_enabled = True
    if row:
        footer_enabled = json.loads(row["value"])
    return body + GMAIL_FOOTER if footer_enabled else body


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
            "draft_reply": ToolVerb(
                name="draft_reply",
                schema={
                    "type": "object",
                    "properties": {
                        "threadId": {"type": "string", "description": "Gmail thread ID to reply to"},
                        "body": {"type": "string", "description": "Draft reply body text"},
                    },
                    "required": ["threadId", "body"],
                },
                auth_class="kinetic",
                reversibility=0.95,
                description_for_model="Create a Gmail draft reply to a thread. Does not send — requires separate confirmation.",
                description_for_human="Draft Gmail reply",
                reverse_verb="delete_draft",
            ),
            "send_reply": ToolVerb(
                name="send_reply",
                schema={
                    "type": "object",
                    "properties": {
                        "draftId": {"type": "string", "description": "Gmail draft ID to send"},
                    },
                    "required": ["draftId"],
                },
                auth_class="kinetic-sensitive",
                reversibility=0.0,
                description_for_model="Send a Gmail draft. Requires explicit confirmation. Cannot be undone.",
                description_for_human="Send Gmail reply",
            ),
        }

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "draft_reply":
            preview = args.get("body", "")[:80]
            return f"Would draft reply to thread {args.get('threadId', '?')}: \"{preview}\""
        if verb == "send_reply":
            return f"Would send draft {args.get('draftId', '?')} — this cannot be undone"
        return super().dry_run(verb, args)

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

        if verb == "draft_reply":
            hdrs = await self._headers()
            if not hdrs:
                return ToolResult(success=False, error="No Google token — run OAuth flow first")
            full_body = _build_draft_body(args["body"])
            msg = email.mime.text.MIMEText(full_body, "plain")
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            draft_body = {"message": {"raw": raw, "threadId": args["threadId"]}}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{GMAIL_API}/users/me/drafts",
                    headers={**hdrs, "Content-Type": "application/json"},
                    json=draft_body,
                )
            if resp.status_code == 401:
                new_token = await refresh_token("google")
                if new_token:
                    hdrs = {"Authorization": f"Bearer {new_token['access_token']}", "Content-Type": "application/json"}
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(f"{GMAIL_API}/users/me/drafts", headers=hdrs, json=draft_body)
            if resp.status_code in (200, 201):
                data = resp.json()
                draft_id = data.get("id", "")
                preview = full_body[:100] + ("…" if len(full_body) > 100 else "")
                _register_artifact("gmail_thread", args["threadId"], f"Draft reply to {args['threadId']}", args.get("__action_id") or None)
                return ToolResult(
                    success=True,
                    data={"draftId": draft_id, "preview": preview},
                    artifact_ref=args["threadId"],
                    artifact_kind="gmail_thread",
                )
            return ToolResult(success=False, error=f"Gmail API error: {resp.status_code}")

        if verb == "send_reply":
            return await self._send_reply(args)

        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _send_reply(self, args: dict) -> ToolResult:
        hdrs = await self._headers()
        if not hdrs:
            return ToolResult(success=False, error="No Google token — run OAuth flow first")

        draft_id = args.get("draftId", "")
        if not draft_id:
            return ToolResult(success=False, error="draftId required")

        to_blocked = await self._check_send_blocklist(draft_id, hdrs)
        if to_blocked:
            return ToolResult(
                success=False,
                error=f"Send blocked: recipient {to_blocked!r} is on the dont_send_to list",
            )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{GMAIL_API}/users/me/drafts/send",
                headers={**hdrs, "Content-Type": "application/json"},
                json={"id": draft_id},
            )

        if resp.status_code == 401:
            new_token = await refresh_token("google")
            if new_token:
                hdrs = {"Authorization": f"Bearer {new_token['access_token']}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{GMAIL_API}/users/me/drafts/send",
                        headers=hdrs,
                        json={"id": draft_id},
                    )

        if resp.status_code == 200:
            message_id = resp.json().get("id", "")
            from chloe.state.db import get_connection
            conn = get_connection()
            conn.execute(
                "UPDATE artifact_index SET exists_=1, ref=? WHERE ref=? AND kind='gmail_thread'",
                (message_id, draft_id),
            )
            conn.commit()
            return ToolResult(
                success=True,
                data={"messageId": message_id, "draftId": draft_id},
                artifact_ref=message_id,
                artifact_kind="gmail_thread",
            )
        return ToolResult(success=False, error=f"Gmail API error: {resp.status_code}")

    async def _check_send_blocklist(self, draft_id: str, hdrs: dict) -> str | None:
        """Return the blocked recipient address if any, else None."""
        import json as _json
        from chloe.state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM preferences WHERE key='gmail_dont_send_to'"
        ).fetchone()
        blocked = _json.loads(row["value"]) if row else []
        if not blocked:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GMAIL_API}/users/me/drafts/{draft_id}",
                    headers=hdrs,
                )
            if resp.status_code != 200:
                return None

            draft_data = resp.json()
            headers = draft_data.get("message", {}).get("payload", {}).get("headers", [])
            to_header = next((h["value"] for h in headers if h["name"].lower() == "to"), "")
            for blocked_addr in blocked:
                if blocked_addr.lower() in to_header.lower():
                    return blocked_addr
        except Exception:
            pass
        return None

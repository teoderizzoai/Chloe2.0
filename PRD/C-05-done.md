# C-05 · `tools/gmail.py` — `draft_reply` verb

## Overview

Add `draft_reply(threadId, body)` verb to `chloe/tools/gmail.py`. Creates a Gmail draft (not sent). Auto-appends the `"— sent with help from Chloe"` footer unless `preferences["gmail_footer"] == false`. Auth: `kinetic`. Returns `{draftId, preview}` and writes an `artifact_index` row with `kind="gmail_thread"`.

## Context

Phase B added read verbs to `gmail.py`. This step adds the first write verb — draft creation — as a safe, non-destructive kinetic action. The draft is never auto-sent; `send_reply` (Phase G) requires a separate kinetic-sensitive confirmation step. The footer serves as a disclosure to recipients that Chloe assisted in drafting the reply.

## Implementation

```python
# Add to GmailTool.__init__ verbs:

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
```

```python
# Helper and artifact registration

GMAIL_FOOTER = "\n\n— sent with help from Chloe"

def _register_artifact(kind: str, ref: str, title: str, action_id: str) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO artifact_index (kind, ref, title, created_by_action, exists_) VALUES (?,?,?,?,1)",
        (kind, ref, title, action_id),
    )
    conn.commit()

def _build_draft_body(body: str) -> str:
    from chloe.state.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM preferences WHERE key='gmail_footer'"
    ).fetchone()
    footer_enabled = True
    if row:
        import json
        footer_enabled = json.loads(row["value"])
    return body + GMAIL_FOOTER if footer_enabled else body
```

```python
# In GmailTool.execute():

elif verb == "draft_reply":
    hdrs = await self._headers()
    if not hdrs:
        return ToolResult(success=False, error="No Google token — run OAuth flow first")

    full_body = _build_draft_body(args["body"])

    # Build RFC 2822 message
    import base64
    import email.mime.text
    msg = email.mime.text.MIMEText(full_body, "plain")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    draft_body = {
        "message": {
            "raw": raw,
            "threadId": args["threadId"],
        }
    }

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
        _register_artifact("gmail_thread", args["threadId"], f"Draft reply to {args['threadId']}", args.get("__action_id", ""))
        return ToolResult(
            success=True,
            data={"draftId": draft_id, "preview": preview},
            artifact_ref=args["threadId"],
            artifact_kind="gmail_thread",
        )
    return ToolResult(success=False, error=f"Gmail API error: {resp.status_code}")
```

## Dry-run output

```python
def dry_run(self, verb: str, args: dict) -> str:
    # existing verbs...
    if verb == "draft_reply":
        preview = args.get("body", "")[:80]
        return f"Would draft reply to thread {args.get('threadId', '?')}: \"{preview}\""
    return super().dry_run(verb, args)
```

## Dependencies

- B-05 (existing `GmailTool` with headers, token refresh, `_headers()` method).
- A-01 (`artifact_index` table).
- `preferences` table (for `gmail_footer` key).

## Testing

### Unit tests — `tests/unit/test_gmail_draft.py`

```python
import pytest
import base64
import email
from unittest.mock import patch, MagicMock
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.tools.gmail import GmailTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture
def tool():
    return GmailTool()

@pytest.fixture
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()

def test_dry_run_draft(tool):
    preview = tool.dry_run("draft_reply", {"threadId": "thread123", "body": "Hello there"})
    assert "thread123" in preview
    assert "Hello" in preview

def test_draft_reply_verb_is_kinetic(tool):
    assert tool.verbs["draft_reply"].auth_class == "kinetic"

@pytest.mark.asyncio
async def test_no_token_returns_error(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: None)
    result = await tool.execute("draft_reply", {"threadId": "t1", "body": "Test"})
    assert not result.success
    assert "token" in result.error.lower()

@pytest.mark.asyncio
async def test_footer_appended_by_default(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    captured_body = {}

    async def mock_post(url, headers=None, json=None):
        captured_body.update(json or {})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_abc"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("draft_reply", {"threadId": "t1", "body": "Hello Teo"})

    assert result.success
    assert result.data["draftId"] == "draft_abc"

    # Decode the raw message and check for footer
    raw_b64 = captured_body["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64)
    msg = email.message_from_bytes(raw_bytes)
    body_text = msg.get_payload()
    assert "sent with help from Chloe" in body_text

@pytest.mark.asyncio
async def test_footer_suppressed_when_preference_false(tool, db, monkeypatch):
    import json
    db.execute("INSERT OR REPLACE INTO preferences (key, value) VALUES ('gmail_footer', ?)", (json.dumps(False),))
    db.commit()

    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    captured_body = {}

    async def mock_post(url, headers=None, json=None):
        captured_body.update(json or {})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_xyz"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("draft_reply", {"threadId": "t2", "body": "No footer"})

    assert result.success
    raw_b64 = captured_body["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64)
    msg = email.message_from_bytes(raw_bytes)
    body_text = msg.get_payload()
    assert "Chloe" not in body_text

@pytest.mark.asyncio
async def test_artifact_row_written(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    async def mock_post(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_r1"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        await tool.execute("draft_reply", {"threadId": "thread_abc", "body": "Hi", "__action_id": "act1"})

    row = db.execute("SELECT * FROM artifact_index WHERE ref='thread_abc'").fetchone()
    assert row is not None
    assert row["kind"] == "gmail_thread"
```

### Integration test (`@pytest.mark.live`)

```python
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_draft_reply():
    tool = GmailTool()
    # Get a real thread ID from recent inbox
    recent = await tool.execute("read_recent", {"limit": 1})
    assert recent.success and recent.data["messages"]
    thread_id = recent.data["messages"][0]["threadId"]

    result = await tool.execute("draft_reply", {
        "threadId": thread_id,
        "body": "Chloe integration test — please delete this draft",
    })
    assert result.success
    assert result.data.get("draftId")
    assert "preview" in result.data
```

## Acceptance criteria

- `draft_reply` dry-run returns a preview without hitting the Gmail API.
- Footer `"— sent with help from Chloe"` present in drafted message by default.
- Footer absent when `preferences["gmail_footer"] == false`.
- Artifact row written to `artifact_index` with `kind="gmail_thread"` on success.
- `draft_reply` is `kinetic` auth class.
- 401 triggers `refresh_token("google")` and retries.

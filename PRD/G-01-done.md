# G-01 · `tools/gmail.py` — `send_reply` verb (kinetic-sensitive)

## Overview

Add `send_reply(draftId)` to `GmailTool`. Auth: `kinetic-sensitive`. Hard filter: if draft `to` field contains any address matching `preferences["gmail_dont_send_to"]`, gate denies without sending to confirmation channel. Only sends after `/v1/confirmations/{id}/confirm` is called.

## Context

`draft_reply` (C-05) creates a draft. `send_reply` actually sends it. The separation is important: Chloe can write drafts freely, but sending email on Teo's behalf requires explicit confirmation. The `gmail_dont_send_to` hard filter is a safety guard — certain addresses (Teo's employer, anyone on the blocked list) should never receive Chloe-assisted emails, regardless of confirmation.

## Implementation

```python
# Add to GmailTool.__init__ verbs:

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
    reversibility=0.0,  # Cannot unsend an email
    description_for_model="Send a Gmail draft. Requires explicit confirmation. Cannot be undone.",
    description_for_human="Send Gmail reply",
),
```

```python
# In GmailTool.execute():

elif verb == "send_reply":
    hdrs = await self._headers()
    if not hdrs:
        return ToolResult(success=False, error="No Google token — run OAuth flow first")

    draft_id = args.get("draftId", "")
    if not draft_id:
        return ToolResult(success=False, error="draftId required")

    # Check dont_send_to hard filter
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
        # Update artifact_index — the draft is now sent
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
            artifact_kind="gmail_message",
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

    # Fetch the draft to check recipients
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{GMAIL_API}/users/me/drafts/{draft_id}",
                headers=hdrs,
            )
        if resp.status_code != 200:
            return None  # Can't check, allow

        draft_data = resp.json()
        headers = draft_data.get("message", {}).get("payload", {}).get("headers", [])
        to_header = next((h["value"] for h in headers if h["name"].lower() == "to"), "")

        for blocked_addr in blocked:
            if blocked_addr.lower() in to_header.lower():
                return blocked_addr
    except Exception:
        pass
    return None
```

## Dry-run

```python
def dry_run(self, verb: str, args: dict) -> str:
    if verb == "send_reply":
        return f"Would send draft {args.get('draftId', '?')} — this cannot be undone"
    return super().dry_run(verb, args)
```

## Dependencies

- C-05 (`draft_reply` — creates the draft that `send_reply` sends).
- C-07 (`confirm.py` — confirmation ticket lifecycle; `send_reply` goes through gate which creates ticket).
- B-03 (Google OAuth scopes — `gmail.modify` required to send).

Note: `gmail.send` scope is different from `gmail.modify`. Sending a draft uses `gmail.modify`. Ensure the OAuth scope includes at minimum `https://www.googleapis.com/auth/gmail.modify`.

## Testing

### Unit tests — `tests/unit/test_gmail_send.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.state.db import migrate, close, get_connection
from chloe.tools.gmail import GmailTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()

@pytest.fixture
def tool():
    return GmailTool()

def test_send_reply_verb_is_kinetic_sensitive(tool):
    assert tool.verbs["send_reply"].auth_class == "kinetic-sensitive"

def test_send_reply_reversibility_zero(tool):
    assert tool.verbs["send_reply"].reversibility == 0.0

def test_dry_run_send_reply(tool):
    preview = tool.dry_run("send_reply", {"draftId": "draft_abc"})
    assert "draft_abc" in preview
    assert "cannot be undone" in preview.lower()

@pytest.mark.asyncio
async def test_send_reply_success(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    sent_to = []

    async def mock_post(url, headers=None, json=None):
        sent_to.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "msg_sent_001"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: {
                "message": {"payload": {"headers": [{"name": "To", "value": "alice@example.com"}]}}
            })
        )
        result = await tool.execute("send_reply", {"draftId": "draft_123"})

    assert result.success
    assert result.data["messageId"] == "msg_sent_001"


@pytest.mark.asyncio
async def test_send_reply_blocked_by_dont_send_to(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("gmail_dont_send_to", json.dumps(["boss@company.com"]))
    )
    db.commit()

    async def mock_get(url, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"payload": {"headers": [
                {"name": "To", "value": "boss@company.com"}
            ]}}
        }
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        mock_client.return_value.__aenter__.return_value.post = AsyncMock()
        result = await tool.execute("send_reply", {"draftId": "draft_blocked"})

    assert not result.success
    assert "blocked" in result.error.lower()


@pytest.mark.asyncio
async def test_send_reply_no_token(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: None)
    result = await tool.execute("send_reply", {"draftId": "draft_x"})
    assert not result.success
```

## Acceptance criteria

- `send_reply` has `auth_class="kinetic-sensitive"` and `reversibility=0.0`.
- Successful send → `data["messageId"]` returned, artifact updated in `artifact_index`.
- Recipient on `gmail_dont_send_to` list → blocked before any API call.
- Missing token → error without exception.
- 401 → token refresh → retry.

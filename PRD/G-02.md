# G-02 · End-to-end email send canary

## Overview

Manual UAT script that verifies the full email send pipeline end-to-end: create a Gmail draft via `draft_reply`, receive a confirmation push, confirm the ticket, `send_reply` executes, and verify the email appears in Gmail Sent. Run once before promoting Phase G to production.

## Context

G-01 implemented the mechanics; G-02 is the proof that they all wire together correctly in a live environment. This is a manual canary, not an automated test, because it touches real Gmail, real push notifications, and the real device. It must be run on the actual production setup (Hetzner VPS + Teo's phone) before Phase G is considered done.

## UAT Script

### Prerequisites

- All Phase C, G-01 are deployed and running.
- Teo's device is registered (`kv["devices"]` has a valid APNs/FCM token).
- Google OAuth token has `gmail.modify` scope.
- A real test recipient email address is available that is NOT on `gmail_dont_send_to`.
- An email thread exists in Gmail inbox to reply to (needed for `draft_reply`'s `threadId`).

### Step 1 — Create a Gmail draft via `draft_reply`

From the chat interface or direct API call:

```python
# POST /v1/chat or direct tool invocation in debug shell:
# chloe shell — then:
from chloe.tools.gmail import GmailTool
tool = GmailTool()
result = await tool.execute("draft_reply", {
    "threadId": "<REAL_THREAD_ID>",
    "to": "canary-test@example.com",        # Use a test address
    "subject": "Re: Canary test",
    "body": "This is an automated canary test reply. Please ignore.",
})
print(result)
# Expected: result.success == True, result.data["draftId"] is set
DRAFT_ID = result.data["draftId"]
print(f"Draft ID: {DRAFT_ID}")
```

Checkpoint: Open Gmail → Drafts. Confirm the draft exists with the auto-footer "— sent with help from Chloe".

### Step 2 — Trigger `send_reply` via the chat model

Ask Chloe to send the draft:

```
User: "Chloe, please send the draft you just created."
```

Expected behavior:
1. Gate receives `send_reply` action (`auth_class="kinetic-sensitive"`).
2. Gate creates a `ConfirmationTicket` and calls `confirm.send()`.
3. A push notification arrives on Teo's phone with title "Action Needed: Send Email".

Checkpoint: Push notification received on device within 30 seconds.

### Step 3 — Confirm via mobile app (Confirmations tab)

1. Open the Chloe mobile app.
2. Navigate to the **Confirmations** tab.
3. Verify the ticket is visible with:
   - Action description: "Send Gmail reply"
   - Preview: "Would send draft `<DRAFT_ID>` — this cannot be undone"
4. Tap **Confirm**.

Expected: The ticket disappears from the Confirmations tab within 2 seconds.

### Step 4 — Verify `send_reply` executed

```python
# In debug shell — check action state:
from chloe.state.db import get_connection
conn = get_connection()
row = conn.execute(
    "SELECT state, artifact_ref FROM actions WHERE tool='gmail' AND verb='send_reply' ORDER BY created_at DESC LIMIT 1"
).fetchone()
print(dict(row))
# Expected: {"state": "confirmed", "artifact_ref": "<message_id>"}
```

### Step 5 — Verify email appears in Gmail Sent

```python
# Check via Gmail API:
import httpx
from chloe.state.oauth_tokens import load as load_token
token = load_token("google")
headers = {"Authorization": f"Bearer {token['access_token']}"}
async with httpx.AsyncClient() as client:
    resp = await client.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers,
        params={"labelIds": "SENT", "maxResults": 5},
    )
messages = resp.json().get("messages", [])
print(messages)  # The sent message ID should appear here
```

Also manually open Gmail → Sent to visually confirm the email arrived.

### Step 6 — Verify artifact_index updated

```python
from chloe.state.db import get_connection
conn = get_connection()
row = conn.execute(
    "SELECT ref, kind, exists_ FROM artifact_index WHERE kind='gmail_message' ORDER BY id DESC LIMIT 1"
).fetchone()
print(dict(row))
# Expected: {"ref": "<message_id>", "kind": "gmail_message", "exists_": 1}
```

### Step 7 — Verify blocklist guard (negative test)

```python
# Insert boss@company.com into blocklist:
import json
conn.execute(
    "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
    ("gmail_dont_send_to", json.dumps(["boss@company.com"]))
)
conn.commit()

# Attempt to create a draft to the blocked address:
result = await tool.execute("draft_reply", {
    "threadId": "<ANOTHER_THREAD_ID>",
    "to": "boss@company.com",
    "subject": "Re: Test",
    "body": "Should be blocked.",
})
BLOCKED_DRAFT_ID = result.data["draftId"]

# Now try to send it:
send_result = await tool.execute("send_reply", {"draftId": BLOCKED_DRAFT_ID})
print(send_result)
# Expected: send_result.success == False, "blocked" in send_result.error.lower()
```

## Checklist

```
[ ] 1. draft_reply creates draft with auto-footer in Gmail Drafts
[ ] 2. send_reply (kinetic-sensitive) triggers push notification within 30s
[ ] 3. Confirmation ticket visible in mobile Confirmations tab
[ ] 4. Tapping "Confirm" removes ticket from Confirmations tab
[ ] 5. actions table shows state="confirmed" for send_reply
[ ] 6. Email appears in Gmail → Sent folder
[ ] 7. artifact_index has gmail_message row with exists_=1
[ ] 8. Blocklist guard: boss@company.com draft → send blocked with error
[ ] 9. No unexpected exceptions in server logs (structlog output)
```

## Dependencies

- G-01 (`send_reply` verb).
- C-05 (`draft_reply` verb).
- C-07 (`confirm.py` — ticket lifecycle).
- C-10 (push delivery).
- F-M04 (Confirmations tab — confirms via mobile UI).

## Sign-off

When all 9 checklist items are checked, record in the UAT log:

```
G-02 PASSED — <date> — Teo
Canary email: sent to canary-test@example.com, subject "Re: Canary test"
Message ID: <Gmail message ID>
Action ID: <action ULID>
```

## Acceptance criteria

- All 9 checklist items pass in a single run against the live environment.
- UAT log entry written and committed to `docs/UAT.md`.
- No server errors in logs during the canary run.

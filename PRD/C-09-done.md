# C-09 · FCM push client — `channels/push_fcm.py`

## Overview

Implement `chloe/channels/push_fcm.py` with `send_push(device_token, payload)` using the FCM v1 API (OAuth2 service account auth). Mirror the same payload interface as C-08. Provide a `preferred_push(device_info, payload)` dispatcher in `channels/push.py` that selects APNs or FCM based on the stored device platform.

## Context

Android users (if any) require FCM instead of APNs. The `preferred_push` dispatcher abstracts the platform choice so the rest of the system can call a single function. FCM v1 API uses Google service account OAuth2 (not the deprecated server key), requiring a service account JSON file stored securely.

## Configuration

```python
# Add to config.py Settings:
fcm_service_account_path: str = ""  # Path to Firebase service account JSON
fcm_project_id: str = ""            # Firebase project ID
```

## Implementation

```python
# chloe/channels/push_fcm.py

import json
import httpx
from pathlib import Path
from datetime import datetime, timedelta
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("push.fcm")

FCM_BASE = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
_token_cache: dict = {}


async def _get_access_token() -> str | None:
    """Get OAuth2 access token for FCM v1 API via service account."""
    now = datetime.utcnow()
    if _token_cache.get("token") and _token_cache.get("expires_at", now) > now:
        return _token_cache["token"]

    settings = get_settings()
    sa_path = Path(settings.fcm_service_account_path)
    if not sa_path.exists():
        log.warning("fcm_sa_missing", path=str(sa_path))
        return None

    try:
        import google.auth.transport.requests
        import google.oauth2.service_account
        creds = google.oauth2.service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        _token_cache["token"] = creds.token
        _token_cache["expires_at"] = datetime.utcnow() + timedelta(minutes=55)
        return creds.token
    except Exception as exc:
        log.warning("fcm_auth_error", error=str(exc))
        return None


def _build_message(device_token: str, payload: dict) -> dict:
    """Build FCM v1 message structure."""
    kind = payload.get("type", "message")
    if kind == "message":
        notification = {
            "title": payload.get("title", "Chloe"),
            "body": payload.get("body", ""),
        }
        data = {}
    elif kind == "confirmation":
        notification = {
            "title": "Confirm action",
            "body": payload.get("preview", "Chloe wants to do something"),
        }
        data = {
            "type": "confirmation",
            "ticket_id": payload.get("ticket_id", ""),
            "action_tool": payload.get("action_tool", ""),
            "action_verb": payload.get("action_verb", ""),
        }
    else:
        notification = {"title": "Chloe", "body": str(payload)}
        data = {}

    return {
        "message": {
            "token": device_token,
            "notification": notification,
            "data": {k: str(v) for k, v in data.items()},
            "android": {"priority": "high"},
        }
    }


async def send_push(device_token: str, payload: dict) -> bool:
    """Send push notification via FCM v1. Returns True on success."""
    token = await _get_access_token()
    if not token:
        return False

    settings = get_settings()
    url = FCM_BASE.format(project_id=settings.fcm_project_id)
    message = _build_message(device_token, payload)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=message,
            )

        if resp.status_code == 200:
            log.info("fcm_sent", device_token=device_token[:8] + "…")
            return True
        else:
            log.warning("fcm_error", status=resp.status_code, body=resp.text[:200])
            return False
    except httpx.RequestError as exc:
        log.warning("fcm_network_error", error=str(exc))
        return False
```

## Dispatcher

```python
# chloe/channels/push.py

from chloe.state.kv import get as kv_get
from chloe.observability.logging import get_logger

log = get_logger("push")


async def preferred_push(device_info: dict, payload: dict) -> bool:
    """Dispatch push to APNs or FCM based on device platform."""
    platform = device_info.get("platform", "ios")
    token = device_info.get("token", "")

    if not token:
        log.warning("push_no_token", platform=platform)
        return False

    if platform == "ios":
        from chloe.channels.push_apns import send_push
        return await send_push(token, payload)
    elif platform == "android":
        from chloe.channels.push_fcm import send_push
        return await send_push(token, payload)
    else:
        log.warning("push_unknown_platform", platform=platform)
        return False


def get_teo_device_info() -> dict:
    """Load Teo's registered device from kv."""
    devices = kv_get("devices", default=[])
    if not devices:
        return {}
    # Prefer most recently registered device
    return devices[-1]
```

## Dependencies

- `google-auth` — service account OAuth2 for FCM.
- `httpx` — HTTP client.
- C-08 (`push_apns.py` — APNs client mirrored here).
- F-02 (`config.py` — FCM settings).
- F-08 (`kv.py` — device storage).

## Testing

### Unit tests — `tests/unit/test_push_fcm.py`

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from chloe.channels.push_fcm import _build_message, send_push

def test_message_payload_structure():
    msg = _build_message("tok123", {"type": "message", "title": "Hi", "body": "World"})
    assert msg["message"]["token"] == "tok123"
    assert msg["message"]["notification"]["title"] == "Hi"
    assert msg["message"]["android"]["priority"] == "high"

def test_confirmation_payload_structure():
    msg = _build_message("tok123", {
        "type": "confirmation",
        "preview": "Add calendar event?",
        "ticket_id": "tkt1",
        "action_tool": "calendar",
        "action_verb": "add_event",
    })
    assert msg["message"]["data"]["ticket_id"] == "tkt1"
    assert msg["message"]["data"]["type"] == "confirmation"
    assert "Add calendar" in msg["message"]["notification"]["body"]

@pytest.mark.asyncio
async def test_send_push_success(monkeypatch):
    monkeypatch.setattr("chloe.channels.push_fcm._get_access_token", AsyncMock(return_value="access_tok"))
    monkeypatch.setattr("chloe.channels.push_fcm.get_settings", lambda: MagicMock(fcm_project_id="proj1"))

    with patch("httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        result = await send_push("dev_token", {"type": "message", "body": "test"})

    assert result is True

@pytest.mark.asyncio
async def test_send_push_no_token(monkeypatch):
    monkeypatch.setattr("chloe.channels.push_fcm._get_access_token", AsyncMock(return_value=None))
    result = await send_push("dev_token", {"type": "message", "body": "test"})
    assert result is False
```

### Unit tests — `tests/unit/test_push_dispatcher.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from chloe.channels.push import preferred_push

@pytest.mark.asyncio
async def test_ios_dispatches_to_apns(monkeypatch):
    mock_apns = AsyncMock(return_value=True)
    monkeypatch.setattr("chloe.channels.push_apns.send_push", mock_apns)

    result = await preferred_push(
        {"platform": "ios", "token": "ios_tok"},
        {"type": "message", "body": "hi"},
    )
    assert result is True
    mock_apns.assert_called_once_with("ios_tok", {"type": "message", "body": "hi"})

@pytest.mark.asyncio
async def test_android_dispatches_to_fcm(monkeypatch):
    mock_fcm = AsyncMock(return_value=True)
    monkeypatch.setattr("chloe.channels.push_fcm.send_push", mock_fcm)

    result = await preferred_push(
        {"platform": "android", "token": "android_tok"},
        {"type": "message", "body": "hi"},
    )
    assert result is True
    mock_fcm.assert_called_once_with("android_tok", {"type": "message", "body": "hi"})

@pytest.mark.asyncio
async def test_empty_token_returns_false():
    result = await preferred_push({"platform": "ios", "token": ""}, {"type": "message"})
    assert result is False
```

## Acceptance criteria

- `_build_message(token, {"type": "confirmation", ...})` includes `aps.category`-equivalent `data.type="confirmation"` and `ticket_id`.
- `send_push` returns `True` on mocked 200 response.
- No service account file → `send_push` returns `False` without raising.
- `preferred_push({"platform": "ios"}, ...)` routes to APNs.
- `preferred_push({"platform": "android"}, ...)` routes to FCM.

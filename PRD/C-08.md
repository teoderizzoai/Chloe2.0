# C-08 · APNs push client — `channels/push_apns.py`

## Overview

Implement `chloe/channels/push_apns.py` with `send_push(device_token, payload)` that sends push notifications via APNs HTTP/2. Support `type="message"` and `type="confirmation"` payload shapes from PRD §18.3. Use `httpx` with the `.p8` JWT-authenticated APNs endpoint. Handle 410 (device token invalidated) by removing the stored device token.

## Context

APNs is the Apple Push Notification service — required to reach Teo's iPhone. APNs HTTP/2 API uses JWT authentication (a `.p8` key file from Apple Developer portal) rather than a TLS client certificate. The JWT is short-lived (max 60 minutes) and must be regenerated before expiry. Payloads differ between message pushes (badge, sound, display) and confirmation pushes (category, action buttons, deep-link data for the Confirmations tab).

## Configuration

```python
# Add to config.py Settings:
apns_key_id: str = ""           # 10-char key ID from Apple Developer
apns_team_id: str = ""          # 10-char team ID
apns_key_path: str = ""         # Path to .p8 file
apns_bundle_id: str = ""        # e.g. "com.teo.chloe"
apns_environment: str = "production"  # or "sandbox"
```

## Implementation

```python
# chloe/channels/push_apns.py

import time
import json
import httpx
import jwt  # PyJWT
from pathlib import Path
from chloe.config import get_settings
from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set, delete as kv_delete

log = get_logger("push.apns")

APNS_HOST_PROD = "https://api.push.apple.com"
APNS_HOST_SAND = "https://api.sandbox.push.apple.com"
_token_cache: dict = {}  # {token_str, issued_at}


def _apns_jwt() -> str:
    """Generate (or return cached) APNs JWT. Valid for 55 minutes."""
    now = int(time.time())
    if _token_cache.get("token") and now - _token_cache.get("issued_at", 0) < 3300:
        return _token_cache["token"]

    settings = get_settings()
    key_path = Path(settings.apns_key_path)
    if not key_path.exists():
        raise FileNotFoundError(f"APNs .p8 key not found at {key_path}")

    private_key = key_path.read_text()
    token = jwt.encode(
        {"iss": settings.apns_team_id, "iat": now},
        private_key,
        algorithm="ES256",
        headers={"alg": "ES256", "kid": settings.apns_key_id},
    )
    _token_cache["token"] = token
    _token_cache["issued_at"] = now
    return token


def _build_payload(payload: dict) -> dict:
    """Normalize payload into APNs aps structure."""
    kind = payload.get("type", "message")
    if kind == "message":
        return {
            "aps": {
                "alert": {
                    "title": payload.get("title", "Chloe"),
                    "body": payload.get("body", ""),
                },
                "badge": payload.get("badge", 1),
                "sound": "default",
            },
        }
    elif kind == "confirmation":
        return {
            "aps": {
                "alert": {
                    "title": "Confirm action",
                    "body": payload.get("preview", "Chloe wants to do something"),
                },
                "category": "CONFIRMATION",
                "sound": "default",
            },
            "ticket_id": payload.get("ticket_id", ""),
            "action_tool": payload.get("action_tool", ""),
            "action_verb": payload.get("action_verb", ""),
        }
    else:
        return {"aps": {"alert": str(payload)}}


async def send_push(device_token: str, payload: dict) -> bool:
    """Send push notification via APNs. Returns True on success."""
    settings = get_settings()
    host = APNS_HOST_PROD if settings.apns_environment == "production" else APNS_HOST_SAND

    try:
        token = _apns_jwt()
    except FileNotFoundError as e:
        log.warning("apns_key_missing", error=str(e))
        return False

    apns_payload = _build_payload(payload)
    headers = {
        "authorization": f"bearer {token}",
        "apns-topic": settings.apns_bundle_id,
        "apns-push-type": "alert",
        "content-type": "application/json",
    }

    url = f"{host}/3/device/{device_token}"
    try:
        async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=apns_payload)

        if resp.status_code == 200:
            log.info("apns_sent", device_token=device_token[:8] + "…")
            return True
        elif resp.status_code == 410:
            # Token invalidated — remove it
            log.warning("apns_token_invalidated", device_token=device_token[:8] + "…")
            _remove_device_token(device_token)
            return False
        else:
            log.warning("apns_error", status=resp.status_code, body=resp.text[:200])
            return False
    except httpx.RequestError as exc:
        log.warning("apns_network_error", error=str(exc))
        return False


def _remove_device_token(device_token: str) -> None:
    """Remove invalidated device token from kv store."""
    devices = kv_get("devices", default=[])
    devices = [d for d in devices if d.get("token") != device_token]
    kv_set("devices", devices)
```

## Dependencies

- `PyJWT` — JWT generation for APNs authentication.
- `httpx[http2]` — HTTP/2 required by APNs endpoint.
- F-02 (`config.py` — APNs settings).
- F-08 (`kv.py` — device token storage).

## Testing

### Unit tests — `tests/unit/test_push_apns.py`

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from chloe.channels.push_apns import send_push, _build_payload

def test_message_payload_shape():
    payload = _build_payload({"type": "message", "title": "Hello", "body": "World"})
    assert payload["aps"]["alert"]["title"] == "Hello"
    assert payload["aps"]["alert"]["body"] == "World"
    assert payload["aps"]["sound"] == "default"

def test_confirmation_payload_shape():
    payload = _build_payload({
        "type": "confirmation",
        "preview": "Queue track on Spotify?",
        "ticket_id": "ticket_abc",
        "action_tool": "spotify",
        "action_verb": "queue_track",
    })
    assert payload["aps"]["category"] == "CONFIRMATION"
    assert payload["ticket_id"] == "ticket_abc"
    assert payload["action_tool"] == "spotify"
    assert "Queue track" in payload["aps"]["alert"]["body"]

@pytest.mark.asyncio
async def test_send_push_success(monkeypatch, tmp_path):
    # Write a fake .p8 key (real ES256 key for JWT signing)
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    key_path = tmp_path / "apns.p8"
    key_path.write_bytes(pem)

    monkeypatch.setenv("APNS_KEY_ID", "ABCDE12345")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APNS_KEY_PATH", str(key_path))
    monkeypatch.setenv("APNS_BUNDLE_ID", "com.teo.chloe")
    monkeypatch.setenv("APNS_ENVIRONMENT", "sandbox")

    from chloe.config import Settings
    monkeypatch.setattr("chloe.channels.push_apns.get_settings", lambda: Settings(
        apns_key_id="ABCDE12345", apns_team_id="TEAM123456",
        apns_key_path=str(key_path), apns_bundle_id="com.teo.chloe",
        apns_environment="sandbox",
    ))

    with patch("httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        result = await send_push("device_token_abc123", {"type": "message", "body": "Hi"})

    assert result is True

@pytest.mark.asyncio
async def test_send_push_410_removes_token(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    key_path = tmp_path / "apns.p8"
    key_path.write_bytes(pem)

    removed = []

    from chloe.channels import push_apns
    monkeypatch.setattr(push_apns, "_remove_device_token", lambda t: removed.append(t))
    monkeypatch.setattr(push_apns, "get_settings", lambda: MagicMock(
        apns_key_id="K", apns_team_id="T",
        apns_key_path=str(key_path), apns_bundle_id="b",
        apns_environment="sandbox",
    ))

    with patch("httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        result = await send_push("bad_token", {"type": "message", "body": "test"})

    assert result is False
    assert "bad_token" in removed
```

## Acceptance criteria

- `_build_payload({"type": "message", ...})` produces valid APNs `aps.alert` structure.
- `_build_payload({"type": "confirmation", ...})` produces `aps.category = "CONFIRMATION"` and `ticket_id`.
- `send_push` returns `True` on 200 from mocked APNs endpoint.
- 410 response removes device token from `kv` and returns `False`.
- Missing `.p8` key returns `False` without raising.

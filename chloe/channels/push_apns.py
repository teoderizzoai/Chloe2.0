import time

import httpx
import jwt
from pathlib import Path

from chloe.config import get_settings
from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("push.apns")

APNS_HOST_PROD = "https://api.push.apple.com"
APNS_HOST_SAND = "https://api.sandbox.push.apple.com"
_token_cache: dict = {}


def _apns_jwt() -> str:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=apns_payload)

        if resp.status_code == 200:
            log.info("apns_sent", device_token=device_token[:8] + "…")
            return True
        elif resp.status_code == 410:
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
    devices = kv_get("devices", default=[])
    devices = [d for d in devices if d.get("token") != device_token]
    kv_set("devices", devices)

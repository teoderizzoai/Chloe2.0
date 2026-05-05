from datetime import datetime, timedelta

import httpx

from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("push.fcm")

FCM_BASE = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
_token_cache: dict = {}


async def _get_access_token() -> str | None:
    now = datetime.utcnow()
    if _token_cache.get("token") and _token_cache.get("expires_at", now) > now:
        return _token_cache["token"]

    settings = get_settings()
    from pathlib import Path
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
    kind = payload.get("type", "message")
    if kind == "message":
        notification = {
            "title": payload.get("title", "Chloe"),
            "body": payload.get("body", ""),
        }
        data: dict = {}
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

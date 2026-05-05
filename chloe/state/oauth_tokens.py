import json
import base64
import os
from chloe.state import kv
from chloe.observability.logging import get_logger
import httpx

log = get_logger("oauth_tokens")


def _load_master_key() -> bytes:
    from chloe.config import get_settings
    settings = get_settings()

    key_file = settings.chloe_master_key_file
    if key_file and key_file.exists():
        raw = key_file.read_bytes().strip()
        if len(raw) == 32:
            return raw
        return base64.b64decode(raw)

    key_b64 = os.environ.get("CHLOE_MASTER_KEY_INLINE")
    if key_b64:
        return base64.b64decode(key_b64)

    raise RuntimeError("No master key found. Set CHLOE_MASTER_KEY_FILE or CHLOE_MASTER_KEY_INLINE.")


def _encrypt(data: dict) -> str:
    try:
        from nacl.secret import SecretBox
        from nacl.utils import random as nacl_random
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = _load_master_key()[:32]
        nonce = os.urandom(12)
        plaintext = json.dumps(data).encode("utf-8")
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        return base64.b64encode(nonce + ct).decode("ascii")

    key = _load_master_key()
    box = SecretBox(key)
    plaintext = json.dumps(data).encode("utf-8")
    encrypted = box.encrypt(plaintext)
    return base64.b64encode(bytes(encrypted)).decode("ascii")


def _decrypt(ciphertext_b64: str) -> dict:
    try:
        from nacl.secret import SecretBox
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = _load_master_key()[:32]
        raw = base64.b64decode(ciphertext_b64)
        nonce, ct = raw[:12], raw[12:]
        plaintext = AESGCM(key).decrypt(nonce, ct, None)
        return json.loads(plaintext)

    key = _load_master_key()
    box = SecretBox(key)
    raw = base64.b64decode(ciphertext_b64)
    plaintext = bytes(box.decrypt(raw))
    return json.loads(plaintext)


KV_PREFIX = "oauth_token:"


def store(service: str, token_data: dict) -> None:
    ciphertext = _encrypt(token_data)
    kv.set(f"{KV_PREFIX}{service}", ciphertext)
    log.info("oauth_token_stored", service=service)


def load(service: str) -> dict | None:
    ciphertext = kv.get(f"{KV_PREFIX}{service}")
    if not ciphertext:
        return None
    try:
        return _decrypt(ciphertext)
    except Exception as e:
        log.error("oauth_token_decrypt_failed", service=service, error=str(e))
        return None


async def refresh(service: str) -> dict | None:
    token = load(service)
    if not token or "refresh_token" not in token:
        log.warning("oauth_no_refresh_token", service=service)
        return None

    if service == "spotify":
        new_token = await _refresh_spotify(token)
    elif service == "google":
        new_token = await _refresh_google(token)
    else:
        log.error("oauth_unknown_service", service=service)
        return None

    if new_token:
        store(service, new_token)
    return new_token


async def _refresh_spotify(token: dict) -> dict | None:
    from chloe.config import get_settings
    import base64 as b64
    s = get_settings()
    client_id = s.spotify_client_id
    client_secret = s.spotify_client_secret.get_secret_value() if s.spotify_client_secret else ""

    creds = b64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
            headers={"Authorization": f"Basic {creds}"},
        )
        if resp.status_code != 200:
            log.error("spotify_refresh_failed", status=resp.status_code)
            return None
        data = resp.json()
        if "refresh_token" not in data:
            data["refresh_token"] = token["refresh_token"]
        return data


async def _refresh_google(token: dict) -> dict | None:
    from chloe.config import get_settings
    s = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret.get_secret_value() if s.google_client_secret else "",
            },
        )
        if resp.status_code != 200:
            log.error("google_refresh_failed", status=resp.status_code)
            return None
        data = resp.json()
        if "refresh_token" not in data:
            data["refresh_token"] = token["refresh_token"]
        return data

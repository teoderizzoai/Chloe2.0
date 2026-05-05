# B-01 · OAuth token storage layer

## Overview

Implement `chloe/state/oauth_tokens.py` with `store(service, token_data)`, `load(service)`, and `refresh(service)`. Encrypts tokens with `libsodium.secretbox` using the master key. Persists to `kv`. Never logs decrypted tokens.

## Context

All vendor integrations (Spotify, Google) require OAuth tokens. These tokens must be encrypted at rest (PRD §21.1). The master key lives in `/etc/chloe/master.key` (owned by root, 600 permissions). In development, the key can be a base64-encoded random 32-byte string in the env var `CHLOE_MASTER_KEY` (inline, not in a file).

## Implementation

```python
# chloe/state/oauth_tokens.py

import json
import base64
import os
from pathlib import Path
from chloe.state import kv
from chloe.observability.logging import get_logger
import httpx

log = get_logger("oauth_tokens")

def _load_master_key() -> bytes:
    """Load the 32-byte master key for secretbox encryption."""
    from chloe.config import get_settings
    settings = get_settings()
    
    # Try file-based key first
    key_file = settings.chloe_master_key_file
    if key_file and key_file.exists():
        raw = key_file.read_bytes().strip()
        # Key file may be raw bytes (32) or base64-encoded (44 chars)
        if len(raw) == 32:
            return raw
        return base64.b64decode(raw)
    
    # Fall back to env var (for development)
    key_b64 = os.environ.get("CHLOE_MASTER_KEY_INLINE")
    if key_b64:
        return base64.b64decode(key_b64)
    
    raise RuntimeError("No master key found. Set CHLOE_MASTER_KEY_FILE or CHLOE_MASTER_KEY_INLINE.")

def _encrypt(data: dict) -> str:
    """Encrypt a dict to a base64 string using libsodium secretbox."""
    try:
        from nacl.secret import SecretBox
        from nacl.utils import random as nacl_random
    except ImportError:
        # Fallback: use cryptography library
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
    """Decrypt a base64 ciphertext back to dict."""
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
    """
    Encrypt and store token_data for service.
    token_data typically: {access_token, refresh_token, expires_at, scope, ...}
    """
    ciphertext = _encrypt(token_data)
    kv.set(f"{KV_PREFIX}{service}", ciphertext)
    log.info("oauth_token_stored", service=service)
    # NEVER log token values — the logger's redact processor handles the rest

def load(service: str) -> dict | None:
    """
    Load and decrypt token for service. Returns None if not stored.
    """
    ciphertext = kv.get(f"{KV_PREFIX}{service}")
    if not ciphertext:
        return None
    try:
        return _decrypt(ciphertext)
    except Exception as e:
        log.error("oauth_token_decrypt_failed", service=service, error=str(e))
        return None

async def refresh(service: str) -> dict | None:
    """
    Refresh the token for service using its refresh_token.
    Calls the appropriate vendor token endpoint.
    Stores the new token on success.
    Returns the new token dict or None on failure.
    """
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
        # Preserve the refresh_token if Spotify doesn't return a new one
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
```

## Dependencies

- F-08 (`kv.set/get`).
- F-09 (logging — with token redaction).
- F-04 (`kv` table exists).
- `PyNaCl` or `cryptography` library (add to `pyproject.toml`).

## Testing

### Unit tests — `tests/unit/test_oauth_tokens.py`

```python
import pytest
import os
import base64
from pathlib import Path

@pytest.fixture(autouse=True)
def set_master_key(monkeypatch):
    """Use a test key so we don't need the actual key file."""
    key = os.urandom(32)
    monkeypatch.setenv("CHLOE_MASTER_KEY_INLINE", base64.b64encode(key).decode())

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    from chloe.state.db import migrate, close
    from pathlib import Path
    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()

def test_store_and_load_roundtrip():
    from chloe.state.oauth_tokens import store, load
    token = {
        "access_token": "tok_abc123",
        "refresh_token": "ref_xyz",
        "expires_in": 3600,
    }
    store("spotify", token)
    loaded = load("spotify")
    assert loaded is not None
    assert loaded["access_token"] == "tok_abc123"
    assert loaded["refresh_token"] == "ref_xyz"

def test_load_returns_none_when_not_stored():
    from chloe.state.oauth_tokens import load
    assert load("nonexistent_service") is None

def test_stored_token_not_plaintext_in_kv():
    from chloe.state import kv
    from chloe.state.oauth_tokens import store
    token = {"access_token": "supersecret_tok_abc"}
    store("test_service", token)
    raw = kv.get("oauth_token:test_service")
    # The raw KV value should NOT contain the plaintext token
    assert "supersecret_tok_abc" not in str(raw)

def test_logs_do_not_contain_token(caplog):
    import logging
    from chloe.state.oauth_tokens import store
    token = {"access_token": "DO_NOT_LOG_ME"}
    with caplog.at_level(logging.INFO):
        store("test_service2", token)
    assert "DO_NOT_LOG_ME" not in caplog.text
```

## Acceptance criteria

- `store` → `load` round-trip returns identical dict.
- Raw KV value does not contain the plaintext token (encrypted).
- Logs contain no token values (checked by inspecting log output).
- `load` on a non-existent service returns `None`.

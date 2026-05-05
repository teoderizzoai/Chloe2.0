# F-02 · `config.py` — env vars and feature flags

## Overview

Implement `chloe/config.py` using `pydantic-settings`. Reads every environment variable from PRD Appendix C into a single typed `Settings` dataclass. Provides a `get_settings()` singleton. Includes a `FEATURE_FLAGS` dict for toggling tools on/off at runtime without code changes.

## Context

The current codebase reads env vars ad hoc throughout `chloe.py`, `llm.py`, and `discord_bot.py`. 2.0 centralises all config in one place, making validation explicit and preventing the app from booting with missing required keys. `pydantic-settings` gives free `.env` file loading, type coercion, and validation errors with field names.

## Settings fields

All fields from PRD Appendix C, grouped by concern:

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── LLM ──────────────────────────────────────────────────────────────
    gemini_api_key: SecretStr
    gemini_project_id: str | None = None
    gemini_use_vertex: bool = False

    # ── Spotify ───────────────────────────────────────────────────────────
    spotify_client_id: str | None = None
    spotify_client_secret: SecretStr | None = None
    spotify_redirect_uri: str = "http://localhost:8000/admin/oauth/spotify/callback"

    # ── Google ────────────────────────────────────────────────────────────
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:8000/admin/oauth/google/callback"

    # ── Twilio ────────────────────────────────────────────────────────────
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None

    # ── APNs ──────────────────────────────────────────────────────────────
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_bundle_id: str = "com.teo.chloe"
    apns_key_file: Path | None = None

    # ── FCM ───────────────────────────────────────────────────────────────
    fcm_service_account_file: Path | None = None

    # ── HomeAssistant ─────────────────────────────────────────────────────
    ha_base_url: str | None = None
    ha_token: SecretStr | None = None

    # ── TTS ───────────────────────────────────────────────────────────────
    eleven_api_key: SecretStr | None = None
    cartesia_api_key: SecretStr | None = None

    # ── Search ────────────────────────────────────────────────────────────
    brave_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None

    # ── STT ───────────────────────────────────────────────────────────────
    whisper_mode: Literal["local", "deepgram"] = "local"
    deepgram_api_key: SecretStr | None = None

    # ── Paths ─────────────────────────────────────────────────────────────
    chloe_db_path: Path = Path("/var/chloe/db/chloe.db")
    chloe_chroma_dir: Path = Path("/var/chloe/chroma")
    chloe_workspace_dir: Path = Path("/var/chloe/workspace")
    chloe_notes_dir: Path = Path("/var/chloe/notes")
    chloe_master_key_file: Path = Path("/etc/chloe/master.key")

    # ── App behaviour ─────────────────────────────────────────────────────
    chloe_timezone: str = "Europe/Rome"
    chloe_daily_usd_cap: float = 1.50
    chloe_log_level: str = "INFO"
    chloe_otel_endpoint: str | None = None

    # ── Debug ─────────────────────────────────────────────────────────────
    dry_run: bool = False
    discord_enabled: bool = True
```

## FEATURE_FLAGS

```python
FEATURE_FLAGS: dict[str, bool] = {
    "spotify":      True,
    "gmail":        True,
    "calendar":     True,
    "reminders":    True,
    "smart_home":   False,   # enabled in Phase G
    "maps":         False,   # enabled in Phase C
    "code_runner":  False,   # enabled in Phase C
    "voice":        False,   # enabled in Phase F
}
```

Feature flags are read at tool-registry boot and gate-submission time. A disabled tool's `execute()` raises `FeatureDisabledError`.

## Singleton

```python
_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

Do NOT use `functools.lru_cache` — it makes test isolation impossible. Use a module-level `None` sentinel so tests can monkeypatch `chloe.config._settings = None` to reset between test cases.

## Implementation notes

- `SecretStr` fields are never logged (pydantic redacts them to `**`). This satisfies PRD §21.1.
- `dry_run` maps to the `DRY_RUN` env var. When true, all kinetic tools return their `dry_run()` preview instead of hitting vendor APIs.
- `chloe_db_path` should be created by the DB layer (F-03), not here.
- For local dev, all optional fields (Spotify, Google, etc.) default to `None`. Tools that need them check at first use.

## Dependencies

- F-01 (package structure must exist).

## Testing

### Unit tests — `tests/unit/test_config.py`

```python
import pytest
from pydantic import ValidationError

def test_missing_gemini_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import chloe.config as cfg
    cfg._settings = None   # reset singleton
    with pytest.raises(ValidationError) as exc:
        cfg.get_settings()
    assert "gemini_api_key" in str(exc.value)

def test_defaults_applied(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    import chloe.config as cfg
    cfg._settings = None
    s = cfg.get_settings()
    assert s.chloe_daily_usd_cap == 1.50
    assert s.dry_run is False
    assert s.whisper_mode == "local"

def test_dry_run_flag(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("DRY_RUN", "true")
    import chloe.config as cfg
    cfg._settings = None
    assert cfg.get_settings().dry_run is True

def test_feature_flags_dict_present():
    from chloe.config import FEATURE_FLAGS
    assert "spotify" in FEATURE_FLAGS
    assert isinstance(FEATURE_FLAGS["spotify"], bool)

def test_singleton_returns_same_instance(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    import chloe.config as cfg
    cfg._settings = None
    a = cfg.get_settings()
    b = cfg.get_settings()
    assert a is b
```

### Required tests: one per required key

Write a parametrized test that removes each required key one at a time and asserts `ValidationError` is raised.

## Acceptance criteria

- Unit test suite green.
- `python -c "from chloe.config import get_settings; get_settings()"` raises `ValidationError` if `GEMINI_API_KEY` is unset.
- Every required key from PRD Appendix C is present in `Settings`.

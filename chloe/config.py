from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_ENV = os.environ.get


@dataclass
class Settings:
    dry_run: bool = False
    discord_enabled: bool = True
    chloe_notes_dir: Path = field(default_factory=lambda: Path.home() / "chloe_notes")
    chloe_workspace_dir: Path = field(default_factory=lambda: Path.home() / "chloe_workspace")
    chloe_master_key_file: Path | None = None
    spotify_client_id: str = ""
    spotify_client_secret: object = None
    spotify_redirect_uri: str = "http://localhost:8000/admin/oauth/spotify/callback"
    google_client_id: str = ""
    google_client_secret: object = None
    google_redirect_uri: str = "http://localhost:8000/admin/oauth/google/callback"
    chloe_timezone: str = "UTC"
    # APNs (C-08)
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_key_path: str = ""
    apns_bundle_id: str = ""
    apns_environment: str = "production"
    # FCM (C-09)
    fcm_service_account_path: str = ""
    fcm_project_id: str = ""

    def __post_init__(self) -> None:
        if _ENV("CHLOE_DRY_RUN", "").lower() in ("1", "true", "yes"):
            self.dry_run = True
        if _ENV("DISCORD_ENABLED", "true").lower() in ("0", "false", "no"):
            self.discord_enabled = False
        if env_path := _ENV("CHLOE_NOTES_DIR"):
            self.chloe_notes_dir = Path(env_path)
        if env_path := _ENV("CHLOE_WORKSPACE_DIR"):
            self.chloe_workspace_dir = Path(env_path)
        if key_file := _ENV("CHLOE_MASTER_KEY_FILE"):
            self.chloe_master_key_file = Path(key_file)
        if redirect_uri := _ENV("SPOTIFY_REDIRECT_URI"):
            self.spotify_redirect_uri = redirect_uri
        if redirect_uri := _ENV("GOOGLE_REDIRECT_URI"):
            self.google_redirect_uri = redirect_uri
        if tz := _ENV("CHLOE_TIMEZONE"):
            self.chloe_timezone = tz
        if v := _ENV("APNS_KEY_ID"):
            self.apns_key_id = v
        if v := _ENV("APNS_TEAM_ID"):
            self.apns_team_id = v
        if v := _ENV("APNS_KEY_PATH"):
            self.apns_key_path = v
        if v := _ENV("APNS_BUNDLE_ID"):
            self.apns_bundle_id = v
        if v := _ENV("APNS_ENVIRONMENT"):
            self.apns_environment = v
        if v := _ENV("FCM_SERVICE_ACCOUNT_PATH"):
            self.fcm_service_account_path = v
        if v := _ENV("FCM_PROJECT_ID"):
            self.fcm_project_id = v


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


FEATURE_FLAGS: dict[str, bool] = {}

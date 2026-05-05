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

    def __post_init__(self) -> None:
        if _ENV("CHLOE_DRY_RUN", "").lower() in ("1", "true", "yes"):
            self.dry_run = True
        if _ENV("DISCORD_ENABLED", "true").lower() in ("0", "false", "no"):
            self.discord_enabled = False
        if env_path := _ENV("CHLOE_NOTES_DIR"):
            self.chloe_notes_dir = Path(env_path)
        if env_path := _ENV("CHLOE_WORKSPACE_DIR"):
            self.chloe_workspace_dir = Path(env_path)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


FEATURE_FLAGS: dict[str, bool] = {}

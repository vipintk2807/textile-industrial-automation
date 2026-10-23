"""Runtime locations. Config, database and logs live outside the source tree."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_CONFIG = "TEXTILE_AUTOMATION_CONFIG"
ENV_DATA_DIR = "TEXTILE_AUTOMATION_DATA_DIR"
ENV_LOG_LEVEL = "TEXTILE_AUTOMATION_LOG_LEVEL"
APP_DIR_NAME = "TextileIndustrialAutomation"
DB_FILE_NAME = "textile_automation.db"
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def default_data_dir(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    local = env.get("LOCALAPPDATA")
    if local:  # Windows
        return Path(local) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


@dataclass(frozen=True)
class RuntimePaths:
    data_dir: Path
    database: Path
    log_dir: Path

    @classmethod
    def resolve(cls, data_dir: Path | None = None, database: Path | None = None) -> RuntimePaths:
        base = data_dir if data_dir is not None else default_data_dir()
        return cls(
            data_dir=base,
            database=database if database is not None else base / DB_FILE_NAME,
            log_dir=base / "logs",
        )


def resolve_config_path(
    explicit: Path | None = None, cwd: Path | None = None, environ: dict[str, str] | None = None
) -> tuple[Path | None, bool]:
    """Return (path, is_example). Order: explicit, env var, config/devices.toml, example."""
    env = os.environ if environ is None else environ
    base = cwd or Path.cwd()
    if explicit is not None:
        return explicit, False
    if env.get(ENV_CONFIG):
        return Path(env[ENV_CONFIG]).expanduser(), False
    primary = base / "config" / "devices.toml"
    if primary.is_file():
        return primary, False
    example = base / "config" / "devices.example.toml"
    if example.is_file():
        return example, True
    return None, False


def resolve_log_level(configured: str, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    level = (env.get(ENV_LOG_LEVEL) or configured).upper()
    return level if level in VALID_LOG_LEVELS else configured

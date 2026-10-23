"""Configuration loading: read file -> parse TOML -> shape check -> semantic build."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from textile_automation.config.build import build_app_config
from textile_automation.config.errors import ConfigError
from textile_automation.domain.errors import SettingIssue
from textile_automation.domain.models import AppConfig


def parse_config(raw: Mapping[str, Any], source: str | None = None) -> AppConfig:
    """Validate an already-parsed configuration mapping."""
    from textile_automation.config.schemas import validate_shape  # needs pydantic

    try:
        validate_shape(raw)
        return build_app_config(raw)
    except ConfigError as exc:
        raise ConfigError(exc.issues, source) from None


def read_toml_file(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file. Accepts UTF-8 with or without a BOM (Windows editors add one).

    Raises ConfigError (never a raw exception) if the file is missing or not valid TOML.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(
            [SettingIssue("", f"Cannot read the configuration file: {exc}")], str(path)
        ) from None
    except UnicodeDecodeError as exc:
        raise ConfigError(
            [SettingIssue("", f"The file is not UTF-8 text: {exc}")], str(path)
        ) from None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([SettingIssue("", f"Not valid TOML: {exc}")], str(path)) from None


def load_config(path: Path) -> AppConfig:
    """Load and validate a TOML configuration file. Raises ConfigError (never a raw exception)."""
    return parse_config(read_toml_file(path), str(path))

"""Pydantic v2 models: structural validation of the parsed TOML (types, required, unknown keys).

Semantic rules (bounds, uniqueness, adapter-specific checks, secrets) live in build.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from textile_automation.config.errors import ConfigError
from textile_automation.domain.errors import SettingIssue


def _require_number(value: Any) -> Any:
    """Booleans and text are not numbers (no silent coercion)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a number")
    return value


def _require_integer(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a whole number")
    return value


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryShape(_Shape):
    initial_delay_s: float | None = None
    max_delay_s: float | None = None
    multiplier: float | None = None
    max_consecutive_failures: int | None = None

    @field_validator("initial_delay_s", "max_delay_s", "multiplier", mode="before")
    @classmethod
    def _numbers(cls, value: Any) -> Any:
        return _require_number(value)

    @field_validator("max_consecutive_failures", mode="before")
    @classmethod
    def _integers(cls, value: Any) -> Any:
        return _require_integer(value)


class MeasurementShape(_Shape):
    key: StrictStr
    name: StrictStr | None = None
    data_type: StrictStr | None = None
    unit: StrictStr | None = None
    min_value: float | None = None
    max_value: float | None = None
    source_mapping: dict[str, Any] | None = None

    @field_validator("min_value", "max_value", mode="before")
    @classmethod
    def _numbers(cls, value: Any) -> Any:
        return _require_number(value)


class DeviceShape(_Shape):
    device_id: StrictStr
    name: StrictStr
    adapter_type: StrictStr
    measurements: list[MeasurementShape]
    device_type: StrictStr | None = None
    enabled: StrictBool | None = None
    poll_interval_s: float | None = None
    connect_timeout_s: float | None = None
    response_timeout_s: float | None = None
    stale_after_intervals: float | None = None
    retry: RetryShape | None = None
    adapter_settings: dict[str, Any] | None = None

    @field_validator(
        "poll_interval_s",
        "connect_timeout_s",
        "response_timeout_s",
        "stale_after_intervals",
        mode="before",
    )
    @classmethod
    def _numbers(cls, value: Any) -> Any:
        return _require_number(value)


class ApplicationShape(_Shape):
    log_level: StrictStr | None = None
    data_dir: StrictStr | None = None
    ui_refresh_seconds: float | None = None
    event_dedup_window_s: float | None = None
    shutdown_timeout_s: float | None = None

    @field_validator(
        "ui_refresh_seconds", "event_dedup_window_s", "shutdown_timeout_s", mode="before"
    )
    @classmethod
    def _numbers(cls, value: Any) -> Any:
        return _require_number(value)


class StorageShape(_Shape):
    database_path: StrictStr | None = None
    retention_days: int | None = None

    @field_validator("retention_days", mode="before")
    @classmethod
    def _integers(cls, value: Any) -> Any:
        return _require_integer(value)


class ConfigShape(_Shape):
    schema_version: StrictInt
    devices: list[DeviceShape]
    application: ApplicationShape | None = None
    storage: StorageShape | None = None


def _location(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def validate_shape(raw: Mapping[str, Any]) -> None:
    """Raise ConfigError listing every structural problem; return None if the shape is valid."""
    try:
        ConfigShape.model_validate(dict(raw))
    except ValidationError as exc:
        issues: list[SettingIssue] = []
        for err in exc.errors():
            message = err["msg"].removeprefix("Value error, ")
            if err["type"] == "missing":
                message = "is required"
            elif err["type"] == "extra_forbidden":
                message = "unknown setting (check spelling)"
            issues.append(SettingIssue(_location(tuple(err["loc"])), message))
        raise ConfigError(issues) from None

"""Semantic validation and construction of the typed configuration (AppConfig).

Input is the parsed TOML mapping (already shape-checked by schemas.py at the loader boundary;
this module also checks types defensively so it can be tested on plain dictionaries).
Every problem is collected and reported together with its path; nothing is guessed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from textile_automation.adapters.registry import ADAPTERS, unsupported_message
from textile_automation.config import limits as L
from textile_automation.config.errors import ConfigError
from textile_automation.domain.errors import SettingIssue
from textile_automation.domain.models import (
    AppConfig,
    DataType,
    DeviceSpec,
    MeasurementSpec,
    RetryPolicy,
    StorageSettings,
)
from textile_automation.paths import VALID_LOG_LEVELS
from textile_automation.redaction import find_sensitive_keys

_DEVICE_ID = re.compile(L.DEVICE_ID_PATTERN)
_MEAS_KEY = re.compile(L.MEASUREMENT_KEY_PATTERN)


class _Ctx:
    def __init__(self) -> None:
        self.issues: list[SettingIssue] = []

    def add(self, path: str, message: str) -> None:
        self.issues.append(SettingIssue(path, message))

    def unknown(self, raw: Mapping[str, Any], allowed: set[str], path: str) -> None:
        for key in sorted(set(raw) - allowed):
            self.add(f"{path}.{key}" if path else key, "unknown setting (check spelling)")

    def number(
        self,
        raw: Mapping[str, Any],
        key: str,
        path: str,
        default: float,
        bounds: tuple[float, float],
    ) -> float:
        if key not in raw:
            return default
        v = raw[key]
        here = f"{path}.{key}"
        if isinstance(v, bool) or not isinstance(v, int | float):
            self.add(here, "must be a number")
        elif not math.isfinite(v):
            self.add(here, "must be a finite number")
        elif not bounds[0] <= v <= bounds[1]:
            self.add(here, f"must be between {bounds[0]:g} and {bounds[1]:g} (got {v:g})")
        else:
            return float(v)
        return default

    def integer(
        self,
        raw: Mapping[str, Any],
        key: str,
        path: str,
        default: int | None,
        bounds: tuple[int, int],
    ) -> int | None:
        if key not in raw:
            return default
        v = raw[key]
        here = f"{path}.{key}"
        if isinstance(v, bool) or not isinstance(v, int):
            self.add(here, "must be a whole number")
        elif not bounds[0] <= v <= bounds[1]:
            self.add(here, f"must be between {bounds[0]} and {bounds[1]} (got {v})")
        else:
            return v
        return default

    def text(
        self,
        raw: Mapping[str, Any],
        key: str,
        path: str,
        *,
        required: bool,
        max_len: int = L.MAX_NAME_LEN,
    ) -> str | None:
        here = f"{path}.{key}"
        if key not in raw:
            if required:
                self.add(here, "is required")
            return None
        v = raw[key]
        if not isinstance(v, str) or not v.strip():
            self.add(here, "must be a non-empty text value")
            return None
        if len(v) > max_len:
            self.add(here, f"must be at most {max_len} characters")
            return None
        return v

    def boolean(self, raw: Mapping[str, Any], key: str, path: str, default: bool) -> bool:
        if key not in raw:
            return default
        if not isinstance(raw[key], bool):
            self.add(f"{path}.{key}", "must be true or false")
            return default
        return raw[key]


def build_app_config(raw: Mapping[str, Any]) -> AppConfig:
    """Validate `raw` and return an AppConfig, or raise ConfigError listing every problem."""
    ctx = _Ctx()
    ctx.unknown(raw, {"schema_version", "application", "storage", "devices"}, "")

    version = raw.get("schema_version")
    if version is None:
        ctx.add("schema_version", "is required (this release supports schema_version = 1)")
    elif version != L.SUPPORTED_SCHEMA_VERSION or isinstance(version, bool):
        ctx.add("schema_version", f"unsupported version {version!r}; this release supports 1")

    app_raw = _table(raw, "application", ctx)
    ctx.unknown(
        app_raw,
        {
            "log_level",
            "data_dir",
            "ui_refresh_seconds",
            "event_dedup_window_s",
            "shutdown_timeout_s",
        },
        "application",
    )
    log_level = app_raw.get("log_level", L.DEFAULT_LOG_LEVEL)
    if not isinstance(log_level, str) or log_level.upper() not in VALID_LOG_LEVELS:
        ctx.add("application.log_level", f"must be one of {', '.join(VALID_LOG_LEVELS)}")
        log_level = L.DEFAULT_LOG_LEVEL
    data_dir = _path(app_raw, "data_dir", "application", ctx)
    ui_refresh = ctx.number(
        app_raw, "ui_refresh_seconds", "application", L.DEFAULT_UI_REFRESH_S, L.UI_REFRESH_BOUNDS_S
    )
    dedup = ctx.number(
        app_raw,
        "event_dedup_window_s",
        "application",
        L.DEFAULT_EVENT_DEDUP_WINDOW_S,
        L.EVENT_DEDUP_BOUNDS_S,
    )
    shutdown = ctx.number(
        app_raw,
        "shutdown_timeout_s",
        "application",
        L.DEFAULT_SHUTDOWN_TIMEOUT_S,
        L.SHUTDOWN_TIMEOUT_BOUNDS_S,
    )

    st_raw = _table(raw, "storage", ctx)
    ctx.unknown(st_raw, {"database_path", "retention_days"}, "storage")
    storage = StorageSettings(
        database_path=_path(st_raw, "database_path", "storage", ctx),
        retention_days=ctx.integer(
            st_raw, "retention_days", "storage", None, L.RETENTION_DAYS_BOUNDS
        ),
    )

    devices_raw = raw.get("devices")
    devices: list[DeviceSpec] = []
    if not isinstance(devices_raw, list) or not devices_raw:
        ctx.add("devices", "at least one [[devices]] entry is required")
    else:
        seen: dict[str, int] = {}
        for i, d in enumerate(devices_raw):
            if not isinstance(d, Mapping):
                ctx.add(f"devices[{i}]", "must be a table")
                continue
            device_spec = _build_device(d, i, ctx)
            did = d.get("device_id")
            if isinstance(did, str):
                if did in seen:
                    ctx.add(
                        f"devices[{i}].device_id",
                        f"duplicate device_id '{did}' (already used by devices[{seen[did]}])",
                    )
                seen.setdefault(did, i)
            if device_spec is not None:
                devices.append(device_spec)

    if ctx.issues:
        raise ConfigError(ctx.issues)
    return AppConfig(
        devices=tuple(devices),
        schema_version=L.SUPPORTED_SCHEMA_VERSION,
        log_level=str(log_level).upper(),
        data_dir=data_dir,
        storage=storage,
        ui_refresh_seconds=ui_refresh,
        event_dedup_window_s=dedup,
        shutdown_timeout_s=shutdown,
    )


def _table(raw: Mapping[str, Any], key: str, ctx: _Ctx) -> Mapping[str, Any]:
    v = raw.get(key, {})
    if not isinstance(v, Mapping):
        ctx.add(key, "must be a table")
        return {}
    return v


def _path(raw: Mapping[str, Any], key: str, path: str, ctx: _Ctx) -> Path | None:
    if key not in raw:
        return None
    v = raw[key]
    if not isinstance(v, str) or not v.strip():
        ctx.add(f"{path}.{key}", "must be a non-empty path string (omit it to use the default)")
        return None
    return Path(v).expanduser()


def _build_device(raw: Mapping[str, Any], i: int, ctx: _Ctx) -> DeviceSpec | None:
    p = f"devices[{i}]"
    before = len(ctx.issues)
    ctx.unknown(
        raw,
        {
            "device_id",
            "name",
            "device_type",
            "adapter_type",
            "enabled",
            "poll_interval_s",
            "connect_timeout_s",
            "response_timeout_s",
            "stale_after_intervals",
            "retry",
            "adapter_settings",
            "measurements",
        },
        p,
    )
    device_id = ctx.text(raw, "device_id", p, required=True, max_len=64)
    if device_id is not None and not _DEVICE_ID.match(device_id):
        ctx.add(
            f"{p}.device_id",
            "may contain letters, digits, '_', '.', '-' and must start "
            "with a letter or digit (max 64 characters)",
        )
    name = ctx.text(raw, "name", p, required=True)
    device_type = ctx.text(raw, "device_type", p, required=False, max_len=64) or "unspecified"
    adapter_type = ctx.text(raw, "adapter_type", p, required=True, max_len=32)
    adapter_cls = None
    if adapter_type is not None:
        adapter_cls = ADAPTERS.get(adapter_type)
        if adapter_cls is None:
            ctx.add(f"{p}.adapter_type", unsupported_message(adapter_type))
    enabled = ctx.boolean(raw, "enabled", p, True)

    lo, hi = (
        (adapter_cls.min_poll_interval_s, adapter_cls.max_poll_interval_s)
        if adapter_cls
        else (0.05, 3600.0)
    )
    poll = ctx.number(raw, "poll_interval_s", p, L.DEFAULT_POLL_INTERVAL_S, (lo, hi))
    connect_t = ctx.number(
        raw, "connect_timeout_s", p, L.DEFAULT_CONNECT_TIMEOUT_S, L.TIMEOUT_BOUNDS_S
    )
    resp_t = ctx.number(
        raw, "response_timeout_s", p, L.DEFAULT_RESPONSE_TIMEOUT_S, L.TIMEOUT_BOUNDS_S
    )
    stale = ctx.number(
        raw, "stale_after_intervals", p, L.DEFAULT_STALE_AFTER_INTERVALS, L.STALE_AFTER_BOUNDS
    )
    retry = _build_retry(_table(raw, "retry", ctx), f"{p}.retry", ctx)

    settings = raw.get("adapter_settings", {})
    if not isinstance(settings, Mapping):
        ctx.add(f"{p}.adapter_settings", "must be a table")
        settings = {}
    for path in find_sensitive_keys(settings):
        ctx.add(
            f"{p}.adapter_settings.{path}",
            "secrets must not be stored in the configuration file; "
            "supply them through environment variables",
        )

    measurements: list[MeasurementSpec] = []
    m_raw = raw.get("measurements")
    if not isinstance(m_raw, list) or not m_raw:
        ctx.add(f"{p}.measurements", "at least one [[devices.measurements]] entry is required")
    else:
        keys: dict[str, int] = {}
        for j, m in enumerate(m_raw):
            if not isinstance(m, Mapping):
                ctx.add(f"{p}.measurements[{j}]", "must be a table")
                continue
            m_spec = _build_measurement(m, f"{p}.measurements[{j}]", ctx)
            key = m.get("key")
            if isinstance(key, str):
                if key in keys:
                    ctx.add(
                        f"{p}.measurements[{j}].key",
                        f"duplicate measurement key '{key}' (also measurements[{keys[key]}])",
                    )
                keys.setdefault(key, j)
            if m_spec is not None:
                measurements.append(m_spec)

    if len(ctx.issues) > before or device_id is None or name is None or adapter_type is None:
        return None
    spec = DeviceSpec(
        device_id=device_id,
        name=name,
        adapter_type=adapter_type,
        measurements=tuple(measurements),
        device_type=device_type,
        enabled=enabled,
        poll_interval_s=poll,
        connect_timeout_s=connect_t,
        response_timeout_s=resp_t,
        stale_after_intervals=stale,
        retry=retry,
        adapter_settings=dict(settings),
    )
    if adapter_cls is not None:
        for issue in adapter_cls.validate_settings(spec):
            ctx.add(f"{p}.{issue.path}" if issue.path else p, issue.message)
    return spec


def _build_retry(raw: Mapping[str, Any], p: str, ctx: _Ctx) -> RetryPolicy:
    ctx.unknown(
        raw, {"initial_delay_s", "max_delay_s", "multiplier", "max_consecutive_failures"}, p
    )
    d = RetryPolicy()
    initial = ctx.number(raw, "initial_delay_s", p, d.initial_delay_s, L.RETRY_DELAY_BOUNDS_S)
    maximum = ctx.number(raw, "max_delay_s", p, d.max_delay_s, L.RETRY_DELAY_BOUNDS_S)
    mult = ctx.number(raw, "multiplier", p, d.multiplier, L.RETRY_MULTIPLIER_BOUNDS)
    limit = ctx.integer(
        raw, "max_consecutive_failures", p, d.max_consecutive_failures, L.RETRY_MAX_FAILURES_BOUNDS
    )
    if maximum < initial:
        ctx.add(f"{p}.max_delay_s", "must be >= initial_delay_s")
    return RetryPolicy(
        initial, maximum, mult, int(limit if limit is not None else d.max_consecutive_failures)
    )


def _build_measurement(raw: Mapping[str, Any], p: str, ctx: _Ctx) -> MeasurementSpec | None:
    before = len(ctx.issues)
    ctx.unknown(
        raw, {"key", "name", "data_type", "unit", "min_value", "max_value", "source_mapping"}, p
    )
    key = ctx.text(raw, "key", p, required=True, max_len=64)
    if key is not None and not _MEAS_KEY.match(key):
        ctx.add(
            f"{p}.key",
            "must start with a lowercase letter and contain only lowercase "
            "letters, digits and '_' (max 64 characters)",
        )
    name = ctx.text(raw, "name", p, required=False) or key
    dt_raw = raw.get("data_type", "float")
    try:
        data_type = DataType(dt_raw)
    except ValueError:
        ctx.add(f"{p}.data_type", "must be one of float, int, bool, str")
        data_type = DataType.FLOAT
    unit = ctx.text(raw, "unit", p, required=False, max_len=L.MAX_UNIT_LEN)
    if unit is not None and not data_type.is_numeric:
        ctx.add(f"{p}.unit", "units apply only to numeric (float/int) measurements")
    min_v = _limit(raw, "min_value", p, ctx, data_type)
    max_v = _limit(raw, "max_value", p, ctx, data_type)
    if min_v is not None and max_v is not None and min_v > max_v:
        ctx.add(f"{p}.max_value", "must be >= min_value")
    mapping = raw.get("source_mapping", {})
    if not isinstance(mapping, Mapping):
        ctx.add(f"{p}.source_mapping", "must be a table")
        mapping = {}
    for path in find_sensitive_keys(mapping):
        ctx.add(
            f"{p}.source_mapping.{path}", "secrets must not be stored in the configuration file"
        )
    if len(ctx.issues) > before or key is None:
        return None
    return MeasurementSpec(key, name or key, data_type, unit, min_v, max_v, dict(mapping))


def _limit(
    raw: Mapping[str, Any], key: str, p: str, ctx: _Ctx, data_type: DataType
) -> float | None:
    if key not in raw:
        return None
    if not data_type.is_numeric:
        ctx.add(f"{p}.{key}", "limits apply only to numeric (float/int) measurements")
        return None
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        ctx.add(f"{p}.{key}", "must be a finite number")
        return None
    return float(v)

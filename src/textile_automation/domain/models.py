"""Typed domain objects: configuration specs, measurement results and events.

These are plain frozen dataclasses so that adapters, services and storage do not depend on the
configuration-parsing library. Pydantic is used only at the configuration boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from textile_automation.domain.errors import ErrorInfo
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import OperatingState, SourceKind

Scalar = bool | int | float | str


class DataType(StrEnum):
    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    STR = "str"

    @property
    def is_numeric(self) -> bool:
        return self in (DataType.FLOAT, DataType.INT)


# ---------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff. `max_consecutive_failures` bounds retries."""

    initial_delay_s: float = 1.0
    max_delay_s: float = 30.0
    multiplier: float = 2.0
    max_consecutive_failures: int = 10


@dataclass(frozen=True)
class MeasurementSpec:
    key: str
    name: str
    data_type: DataType = DataType.FLOAT
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    # Adapter-specific source mapping (for the simulator: synthetic generation parameters).
    source_mapping: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceSpec:
    device_id: str
    name: str
    adapter_type: str
    measurements: tuple[MeasurementSpec, ...]
    device_type: str = "unspecified"
    enabled: bool = True
    poll_interval_s: float = 1.0
    connect_timeout_s: float = 2.0
    response_timeout_s: float = 2.0
    stale_after_intervals: float = 3.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    adapter_settings: Mapping[str, Any] = field(default_factory=dict)

    def measurement(self, key: str) -> MeasurementSpec | None:
        for spec in self.measurements:
            if spec.key == key:
                return spec
        return None


@dataclass(frozen=True)
class StorageSettings:
    database_path: Path | None = None
    retention_days: int | None = None  # None = keep everything (documented default)


@dataclass(frozen=True)
class AppConfig:
    devices: tuple[DeviceSpec, ...]
    schema_version: int = 1
    log_level: str = "INFO"
    data_dir: Path | None = None
    storage: StorageSettings = field(default_factory=StorageSettings)
    ui_refresh_seconds: float = 2.0
    event_dedup_window_s: float = 60.0
    shutdown_timeout_s: float = 5.0


# ---------------------------------------------------------------------------- measurements


@dataclass(frozen=True)
class MeasurementResult:
    """Normalised measurement (hardware communication specification, 6.2)."""

    device_id: str
    measurement_key: str
    value: object | None
    unit: str | None
    received_at: datetime
    source_kind: SourceKind
    quality: Quality = Quality.UNKNOWN
    source_timestamp: datetime | None = None
    raw_value: object | None = None
    error: ErrorInfo | None = None


@dataclass(frozen=True)
class OperatingReport:
    """Machine operating state as reported by the source (synthetic for the simulator)."""

    state: OperatingState
    raw_value: object | None = None
    source_timestamp: datetime | None = None


@dataclass(frozen=True)
class AdapterReadout:
    """Everything one adapter read returned. Absent measurement keys are treated as MISSING."""

    device_id: str
    results: tuple[MeasurementResult, ...]
    operating_report: OperatingReport | None = None


# ---------------------------------------------------------------------------- events


class EventCategory(StrEnum):
    STARTUP = "STARTUP"
    SHUTDOWN = "SHUTDOWN"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    ADAPTER_INIT_FAILURE = "ADAPTER_INIT_FAILURE"
    COMMUNICATION_STATE_CHANGE = "COMMUNICATION_STATE_CHANGE"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    ACQUISITION_FAILURE = "ACQUISITION_FAILURE"
    RECOVERY = "RECOVERY"
    MEASUREMENT_REJECTED = "MEASUREMENT_REJECTED"
    OPERATING_STATE_CHANGE = "OPERATING_STATE_CHANGE"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    PERSISTENCE_RESTORED = "PERSISTENCE_RESTORED"
    RETENTION = "RETENTION"
    SIMULATOR_CONTROL = "SIMULATOR_CONTROL"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Event:
    occurred_at: datetime
    category: EventCategory
    severity: Severity
    message: str
    device_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

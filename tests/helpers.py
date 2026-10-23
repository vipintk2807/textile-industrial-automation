"""Shared test helpers: spec builders, in-memory storage double, dependency markers."""

from __future__ import annotations

import importlib.util
import threading
import time
import unittest
from collections.abc import Callable, Sequence
from datetime import datetime

from textile_automation.domain.errors import StorageError
from textile_automation.domain.models import (
    DataType,
    DeviceSpec,
    Event,
    MeasurementResult,
    MeasurementSpec,
    RetryPolicy,
)


def _has(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


requires_pydantic = unittest.skipUnless(_has("pydantic"), "pydantic not installed")
requires_sqlalchemy = unittest.skipUnless(
    _has("sqlalchemy") and _has("alembic"), "sqlalchemy/alembic not installed"
)
requires_streamlit = unittest.skipUnless(_has("streamlit"), "streamlit not installed")


def meas(
    key: str = "speed",
    *,
    data_type: DataType = DataType.FLOAT,
    unit: str | None = "rpm",
    min_value: float | None = None,
    max_value: float | None = None,
    mapping: dict | None = None,
    name: str | None = None,
) -> MeasurementSpec:
    if mapping is None:
        mapping = {"base": 100.0} if data_type.is_numeric else {}
    return MeasurementSpec(key, name or key.title(), data_type, unit, min_value, max_value, mapping)


def device(
    device_id: str = "dev-1",
    *,
    measurements: Sequence[MeasurementSpec] | None = None,
    enabled: bool = True,
    poll: float = 1.0,
    settings: dict | None = None,
    retry: RetryPolicy | None = None,
    adapter_type: str = "simulator",
    connect_timeout: float = 0.05,
    response_timeout: float = 0.05,
    stale_after: float = 3.0,
) -> DeviceSpec:
    return DeviceSpec(
        device_id=device_id,
        name=f"Device {device_id}",
        adapter_type=adapter_type,
        measurements=tuple(measurements) if measurements is not None else (meas(),),
        enabled=enabled,
        poll_interval_s=poll,
        connect_timeout_s=connect_timeout,
        response_timeout_s=response_timeout,
        stale_after_intervals=stale_after,
        retry=retry
        or RetryPolicy(
            initial_delay_s=1.0, max_delay_s=8.0, multiplier=2.0, max_consecutive_failures=5
        ),
        adapter_settings=settings or {},
    )


class FakeStorage:
    """In-memory StorageBackend with switchable failure (for persistence-failure tests)."""

    def __init__(self) -> None:
        self.measurements: list[MeasurementResult] = []
        self.events: list[Event] = []
        self.devices: dict[str, DeviceSpec] = {}
        self.fail = False
        self.closed = False
        self.save_calls = 0
        self._lock = threading.Lock()

    def _check(self) -> None:
        if self.fail:
            raise StorageError("simulated storage failure")

    def save_measurements(self, results: Sequence[MeasurementResult]) -> int:
        with self._lock:
            self.save_calls += 1
            self._check()
            self.measurements.extend(results)
            return len(results)

    def save_event(self, event: Event) -> None:
        with self._lock:
            self._check()
            self.events.append(event)

    def health_check(self) -> None:
        self._check()

    def upsert_devices(self, devices: Sequence[DeviceSpec], source_kinds: dict[str, str]) -> None:
        self._check()
        for d in devices:
            self.devices[d.device_id] = d

    def latest_measurement(self, device_id: str, key: str) -> MeasurementResult | None:
        rows = [
            m for m in self.measurements if m.device_id == device_id and m.measurement_key == key
        ]
        return max(rows, key=lambda m: m.received_at) if rows else None

    def query_measurements(self, device_id, key, start, end, limit):  # type: ignore[no-untyped-def]
        self._check()
        rows = [
            m
            for m in self.measurements
            if m.device_id == device_id
            and m.measurement_key == key
            and (start is None or m.received_at >= start)
            and (end is None or m.received_at <= end)
        ]
        return sorted(rows, key=lambda m: m.received_at)[-limit:]

    def query_events(
        self, *, device_id=None, categories=None, severities=None, start=None, end=None, limit=200
    ):  # type: ignore[no-untyped-def]
        self._check()
        out = [
            e
            for e in self.events
            if (device_id is None or e.device_id == device_id)
            and (not categories or e.category.value in categories)
            and (not severities or e.severity.value in severities)
        ]
        return list(reversed(out))[:limit]

    def purge_before(self, cutoff: datetime) -> tuple[int, int]:
        n = len(self.measurements)
        self.measurements = [m for m in self.measurements if m.received_at >= cutoff]
        return n - len(self.measurements), 0

    def close(self) -> None:
        self.closed = True


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0, interval: float = 0.01) -> bool:
    """Poll a condition with a hard deadline (no fixed sleeps in tests)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()

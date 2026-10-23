"""Storage interface the services depend on (implemented by storage.repositories.SqlStorage)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from textile_automation.domain.models import DeviceSpec, Event, MeasurementResult


class StorageBackend(Protocol):
    def save_measurements(self, results: Sequence[MeasurementResult]) -> int: ...

    def save_event(self, event: Event) -> None: ...

    def health_check(self) -> None: ...

    def upsert_devices(
        self, devices: Sequence[DeviceSpec], source_kinds: dict[str, str]
    ) -> None: ...

    def latest_measurement(self, device_id: str, key: str) -> MeasurementResult | None: ...

    def query_measurements(
        self, device_id: str, key: str, start: datetime | None, end: datetime | None, limit: int
    ) -> list[MeasurementResult]: ...

    def query_events(
        self,
        *,
        device_id: str | None = None,
        categories: Sequence[str] | None = None,
        severities: Sequence[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]: ...

    def purge_before(self, cutoff: datetime) -> tuple[int, int]: ...

    def close(self) -> None: ...

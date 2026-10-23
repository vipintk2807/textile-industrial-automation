"""Persistence gate: all measurement writes go through here.

Policy (documented restricted mode): if a write fails, the batch is dropped and counted (no
in-memory backlog), the failure is made visible, acquisition continues, and the next cycle
attempts a normal write again. A successful write is the verification that storage works again.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from textile_automation.clock import Clock, SystemClock
from textile_automation.domain.errors import StorageError
from textile_automation.domain.models import EventCategory, MeasurementResult, Severity
from textile_automation.services.events import EventService
from textile_automation.services.ports import StorageBackend

log = logging.getLogger("textile_automation.persistence")


class PersistenceMode(StrEnum):
    NORMAL = "NORMAL"
    RESTRICTED = "RESTRICTED"  # history is NOT being saved


@dataclass(frozen=True)
class PersistenceStatus:
    mode: PersistenceMode
    last_error: str | None
    last_failure_at: datetime | None
    last_success_at: datetime | None
    dropped_measurements: int
    writes_ok: int
    writes_failed: int


class PersistenceGate:
    def __init__(self, storage: StorageBackend, events: EventService, clock: Clock | None = None):
        self._storage = storage
        self._events = events
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._mode = PersistenceMode.NORMAL
        self._last_error: str | None = None
        self._last_failure_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._dropped = 0
        self._dropped_since_failure = 0
        self._ok = 0
        self._failed = 0

    def write(self, results: Sequence[MeasurementResult]) -> bool:
        """Store `results` atomically. Returns True only if they were really stored."""
        if not results:
            return True
        try:
            self._storage.save_measurements(results)
        except StorageError as exc:
            self._on_failure(len(results), str(exc))
            return False
        self._on_success()
        return True

    def _on_failure(self, count: int, error: str) -> None:
        now = self._clock.now()
        with self._lock:
            first = self._mode is PersistenceMode.NORMAL
            self._mode = PersistenceMode.RESTRICTED
            self._failed += 1
            self._dropped += count
            self._dropped_since_failure += count
            self._last_error, self._last_failure_at = error, now
        log.error("Measurement write failed; %d measurement(s) dropped: %s", count, error)
        if first:
            self._events.record(
                EventCategory.PERSISTENCE_ERROR,
                Severity.ERROR,
                "Storage write failed: monitoring continues in restricted mode; "
                "history is NOT being saved.",
                details={"error": error},
                dedup_key="persistence-error",
            )

    def _on_success(self) -> None:
        now = self._clock.now()
        with self._lock:
            restored = self._mode is PersistenceMode.RESTRICTED
            dropped = self._dropped_since_failure
            self._mode = PersistenceMode.NORMAL
            self._ok += 1
            self._last_success_at = now
            self._dropped_since_failure = 0
        if restored:
            self._events.record(
                EventCategory.PERSISTENCE_RESTORED,
                Severity.INFO,
                f"Storage writes succeeded again; {dropped} measurement(s) were not saved.",
                details={"dropped_measurements": dropped},
                dedup_key="persistence-restored",
            )

    def status(self) -> PersistenceStatus:
        with self._lock:
            return PersistenceStatus(
                self._mode,
                self._last_error,
                self._last_failure_at,
                self._last_success_at,
                self._dropped,
                self._ok,
                self._failed,
            )

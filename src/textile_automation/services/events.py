"""Operational event recording with bounded duplicate suppression.

Events are for operators ("what did the monitor observe, and when"); application logs are for
developers. Identical events (same dedup key) inside the dedup window are suppressed and
counted; the next event recorded for that key carries `suppressed_repeats`.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Hashable, Mapping
from datetime import datetime, timedelta
from typing import Any

from textile_automation.clock import Clock, SystemClock
from textile_automation.domain.errors import StorageError
from textile_automation.domain.models import Event, EventCategory, Severity
from textile_automation.redaction import redact_mapping
from textile_automation.services.ports import StorageBackend

log = logging.getLogger("textile_automation.events")
RING_SIZE = 500
MAX_DEDUP_KEYS = 2000
_LEVEL = {
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.ERROR: logging.ERROR,
}


class EventService:
    def __init__(
        self,
        storage: StorageBackend | None = None,
        clock: Clock | None = None,
        dedup_window_s: float = 60.0,
    ) -> None:
        self._storage = storage
        self._clock = clock or SystemClock()
        self._window = timedelta(seconds=dedup_window_s)
        self._lock = threading.Lock()
        self._ring: deque[Event] = deque(maxlen=RING_SIZE)
        self._seen: dict[Hashable, tuple[datetime, int]] = {}
        self.persist_failures = 0
        self.suppressed_total = 0

    def attach_storage(self, storage: StorageBackend | None) -> None:
        self._storage = storage

    def record(
        self,
        category: EventCategory,
        severity: Severity,
        message: str,
        *,
        device_id: str | None = None,
        details: Mapping[str, Any] | None = None,
        dedup_key: Hashable | None = None,
    ) -> Event | None:
        """Record an event. Returns None if it was suppressed as a recent duplicate."""
        now = self._clock.now()
        key = dedup_key if dedup_key is not None else (device_id, category, message)
        full_key = (device_id, category, key)
        info: dict[str, Any] = dict(redact_mapping(dict(details or {})))
        with self._lock:
            first, count = self._seen.get(full_key, (now, 0))
            if full_key in self._seen and now - first < self._window:
                self._seen[full_key] = (first, count + 1)
                self.suppressed_total += 1
                return None
            if count:
                info["suppressed_repeats"] = count
            if len(self._seen) >= MAX_DEDUP_KEYS:
                self._seen.clear()  # bounded memory
            self._seen[full_key] = (now, 0)
            event = Event(now, category, severity, message, device_id, info)
            self._ring.append(event)
        log.log(_LEVEL[severity], message, extra={"device_id": device_id or "-"})
        self._persist(event)
        return event

    def _persist(self, event: Event) -> None:
        storage = self._storage
        if storage is None:
            return
        try:
            storage.save_event(event)
        except StorageError as exc:
            self.persist_failures += 1
            log.error("Event could not be stored: %s", exc)

    def recent(self, limit: int = 100) -> list[Event]:
        """Most recent events held in memory (newest first); independent of the database."""
        with self._lock:
            return list(reversed(self._ring))[:limit]

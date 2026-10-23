"""Application coordinator: owns startup order, the acquisition manager, storage and shutdown.

Startup order (app workflow 5.1): configuration is already validated -> storage ready ->
devices registered -> previous-session values restored (flagged stale) -> acquisition started.
The coordinator contains no protocol logic and no measurement-processing rules.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from textile_automation.adapters.registry import create_adapter
from textile_automation.clock import Clock, SystemClock
from textile_automation.domain.errors import SettingIssue, StorageError
from textile_automation.domain.models import (
    AppConfig,
    Event,
    EventCategory,
    MeasurementResult,
    Severity,
)
from textile_automation.domain.states import SourceKind
from textile_automation.paths import RuntimePaths
from textile_automation.services.acquisition import (
    AcquisitionManager,
    AdapterFactory,
    ShutdownReport,
)
from textile_automation.services.device_state import DeviceSnapshot
from textile_automation.services.events import EventService
from textile_automation.services.persistence import PersistenceGate, PersistenceStatus
from textile_automation.services.pipeline import MeasurementPipeline
from textile_automation.services.ports import StorageBackend

log = logging.getLogger("textile_automation.runtime")
RETENTION_CHECK_INTERVAL_S = 3600.0


class RuntimeStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    FAILED = "FAILED"  # configuration or storage initialisation failed; nothing is acquired
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class RuntimeSnapshot:
    generated_at: datetime
    status: RuntimeStatus
    startup_issues: tuple[SettingIssue, ...]
    config_path: str | None
    using_example_config: bool
    devices: tuple[DeviceSnapshot, ...]
    persistence: PersistenceStatus | None
    event_persist_failures: int
    any_simulated: bool
    any_physical: bool
    database_path: str | None
    log_dir: str | None
    retention_days: int | None
    ui_refresh_seconds: float


class AppRuntime:
    def __init__(
        self,
        config: AppConfig,
        storage: StorageBackend,
        *,
        clock: Clock | None = None,
        adapter_factory: AdapterFactory = create_adapter,
        guard_grace_s: float = 0.5,
        paths: RuntimePaths | None = None,
        config_path: str | None = None,
        using_example_config: bool = False,
    ) -> None:
        self.config = config
        self.storage: StorageBackend | None = storage
        self.clock = clock or SystemClock()
        self.paths = paths
        self.config_path = config_path
        self.using_example_config = using_example_config
        self.events = EventService(storage, self.clock, config.event_dedup_window_s)
        self._gate = PersistenceGate(storage, self.events, self.clock)
        self._pipeline = MeasurementPipeline(self._gate, self.events)
        self.manager: AcquisitionManager | None = AcquisitionManager(
            config,
            self._pipeline,
            self.events,
            self.clock,
            adapter_factory,
            guard_grace_s=guard_grace_s,
        )
        self._issues: tuple[SettingIssue, ...] = ()
        self._status = RuntimeStatus.NOT_STARTED
        self._lock = threading.Lock()
        self._stop_maintenance = threading.Event()
        self._maintenance: threading.Thread | None = None

    @classmethod
    def failed(
        cls,
        issues: Sequence[SettingIssue],
        *,
        config_path: str | None = None,
        using_example_config: bool = False,
        paths: RuntimePaths | None = None,
        clock: Clock | None = None,
    ) -> AppRuntime:
        """A runtime that never acquires: used when configuration or storage cannot be trusted."""
        rt = cls.__new__(cls)
        rt.config = AppConfig(devices=())
        rt.storage = None
        rt.clock = clock or SystemClock()
        rt.paths, rt.config_path, rt.using_example_config = paths, config_path, using_example_config
        rt.events = EventService(None, rt.clock)
        rt._gate = None  # type: ignore[assignment]
        rt._pipeline = None  # type: ignore[assignment]
        rt.manager = None
        rt._issues = tuple(issues)
        rt._status = RuntimeStatus.FAILED
        rt._lock = threading.Lock()
        rt._stop_maintenance = threading.Event()
        rt._maintenance = None
        for issue in rt._issues:
            rt.events.record(
                EventCategory.CONFIGURATION_ERROR, Severity.ERROR, str(issue), dedup_key=str(issue)
            )
        return rt

    @property
    def status(self) -> RuntimeStatus:
        return self._status

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        """Idempotent. Never starts a second acquisition loop."""
        with self._lock:
            if self._status is not RuntimeStatus.NOT_STARTED:
                return
            assert self.manager is not None and self.storage is not None
            try:
                self.manager.register_devices()
                kinds = {did: t.source_kind.value for did, t in self.manager.trackers.items()}
                self.storage.upsert_devices(self.config.devices, kinds)
            except StorageError as exc:
                self._fail(SettingIssue("storage", f"Database is not usable: {exc}"))
                return
            self._restore_previous_session()
            self.events.record(
                EventCategory.STARTUP,
                Severity.INFO,
                f"Monitoring started: {len(self.config.devices)} device(s) configured, "
                f"{len(self.manager.workers)} enabled.",
                details={
                    "simulated_devices": sum(
                        1
                        for t in self.manager.trackers.values()
                        if t.source_kind is SourceKind.SIMULATOR
                    )
                },
            )
            self.manager.start()
            self._status = RuntimeStatus.RUNNING
            if self.config.storage.retention_days:
                self._maintenance = threading.Thread(
                    target=self._retention_loop, name="retention", daemon=True
                )
                self._maintenance.start()

    def _fail(self, issue: SettingIssue) -> None:
        self._issues = (*self._issues, issue)
        self._status = RuntimeStatus.FAILED
        self.events.record(EventCategory.CONFIGURATION_ERROR, Severity.ERROR, str(issue))
        log.error("Startup failed: %s", issue)

    def _restore_previous_session(self) -> None:
        assert self.manager is not None and self.storage is not None
        for spec in self.config.devices:
            restored: list[MeasurementResult] = []
            for m in spec.measurements:
                try:
                    last = self.storage.latest_measurement(spec.device_id, m.key)
                except StorageError as exc:
                    log.warning("Could not restore history for %s: %s", spec.device_id, exc)
                    break
                if last is not None:
                    restored.append(last)
            self.manager.trackers[spec.device_id].restore_previous(restored)

    def _retention_loop(self) -> None:
        days = self.config.storage.retention_days
        assert days and self.storage is not None
        while not self._stop_maintenance.is_set():
            cutoff = self.clock.now() - timedelta(days=days)
            try:
                m, e = self.storage.purge_before(cutoff)
                self.events.record(
                    EventCategory.RETENTION,
                    Severity.INFO,
                    f"Retention policy ({days} days): removed {m} measurement(s), {e} event(s).",
                    details={"cutoff": cutoff.isoformat()},
                    dedup_key=("retention", m, e),
                )
            except StorageError as exc:
                log.error("Retention purge failed: %s", exc)
            self._stop_maintenance.wait(RETENTION_CHECK_INTERVAL_S)

    def shutdown(self) -> ShutdownReport | None:
        """Idempotent, bounded shutdown (app workflow 12)."""
        with self._lock:
            if self._status is RuntimeStatus.STOPPED:
                return None
            was_running = self._status is RuntimeStatus.RUNNING
            self._status = RuntimeStatus.STOPPED
        self._stop_maintenance.set()
        report = None
        if self.manager is not None:
            report = self.manager.shutdown(self.config.shutdown_timeout_s)
        if was_running:
            self.events.record(
                EventCategory.SHUTDOWN,
                Severity.INFO,
                "Monitoring stopped."
                if report is None or report.stopped_cleanly
                else f"Monitoring stopped; threads still running: {report.threads_still_running}",
            )
        storage, self.storage = self.storage, None
        if storage is not None:
            self.events.attach_storage(None)
            try:
                storage.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must complete
                log.warning("Storage close failed: %s", exc)
        return report

    # -- read-only queries for the UI ------------------------------------------------------
    def snapshot(self) -> RuntimeSnapshot:
        devices = tuple(self.manager.snapshots()) if self.manager else ()
        return RuntimeSnapshot(
            generated_at=self.clock.now(),
            status=self._status,
            startup_issues=self._issues,
            config_path=self.config_path,
            using_example_config=self.using_example_config,
            devices=devices,
            persistence=self._gate.status() if self._gate else None,
            event_persist_failures=self.events.persist_failures,
            any_simulated=any(d.source_kind is SourceKind.SIMULATOR for d in devices),
            any_physical=any(d.source_kind is SourceKind.PHYSICAL for d in devices),
            database_path=str(self.paths.database) if self.paths else None,
            log_dir=str(self.paths.log_dir) if self.paths else None,
            retention_days=self.config.storage.retention_days,
            ui_refresh_seconds=self.config.ui_refresh_seconds,
        )

    def history(
        self,
        device_id: str,
        key: str,
        start: datetime | None,
        end: datetime | None,
        limit: int = 5000,
    ) -> list[MeasurementResult]:
        if self.storage is None:
            raise StorageError("Storage is not available.")
        return self.storage.query_measurements(device_id, key, start, end, min(limit, 20000))

    def stored_events(
        self,
        *,
        device_id: str | None = None,
        categories: Sequence[str] | None = None,
        severities: Sequence[str] | None = None,
        start: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        if self.storage is None:
            raise StorageError("Storage is not available.")
        return self.storage.query_events(
            device_id=device_id,
            categories=categories,
            severities=severities,
            start=start,
            limit=min(limit, 2000),
        )

    def retry_device(self, device_id: str) -> bool:
        return self.manager.retry_device(device_id) if self.manager else False

    def inject_simulator_fault(self, device_id: str, **fault: object) -> object:
        if self.manager is None:
            raise ValueError("Monitoring is not running.")
        return self.manager.inject_simulator_fault(device_id, **fault)

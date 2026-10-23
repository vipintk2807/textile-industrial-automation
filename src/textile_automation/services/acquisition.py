"""Acquisition: one DeviceWorker per device (step-driven), one manager that owns the threads.

Design points (technical specification 5.3, 9; app workflow 4.5, 7, 12):
 * One thread per enabled device, so a slow or failing device never blocks another.
 * `DeviceWorker.step()` performs ONE cycle and returns the delay before the next one, so the
   whole logic can be tested deterministically without sleeping.
 * Every adapter call runs through TimedCall with a hard limit, even if the adapter ignores its
   own timeout. A stuck call is abandoned (daemon thread) and no new request is sent to that
   device until it returns: requests never overlap and never accumulate.
 * Retries use bounded exponential backoff and stop at max_consecutive_failures (ERROR).
 * Unexpected exceptions are caught at the worker boundary, logged with traceback, and turn only
   that device into ERROR.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from textile_automation.adapters.base import DeviceAdapter
from textile_automation.adapters.registry import create_adapter
from textile_automation.clock import Clock, SystemClock
from textile_automation.domain.errors import (
    AdapterFailure,
    ErrorCategory,
    ErrorInfo,
    OperationNotPermitted,
)
from textile_automation.domain.models import (
    AppConfig,
    DeviceSpec,
    EventCategory,
    Severity,
)
from textile_automation.domain.states import CommunicationState
from textile_automation.services.device_state import (
    DeviceSnapshot,
    DeviceStateTracker,
    StateChange,
)
from textile_automation.services.events import EventService
from textile_automation.services.pipeline import MeasurementPipeline
from textile_automation.services.timed_call import CallBusy, CallTimeout, TimedCall

log = logging.getLogger("textile_automation.acquisition")
AdapterFactory = Callable[[DeviceSpec, Clock], DeviceAdapter]

_STATE_SEVERITY = {
    CommunicationState.ERROR: Severity.ERROR,
    CommunicationState.DEGRADED: Severity.WARNING,
    CommunicationState.RECOVERING: Severity.WARNING,
    CommunicationState.DISCONNECTED: Severity.WARNING,
}


class DeviceWorker:
    def __init__(
        self,
        spec: DeviceSpec,
        adapter: DeviceAdapter,
        tracker: DeviceStateTracker,
        pipeline: MeasurementPipeline,
        events: EventService,
        clock: Clock,
        *,
        guard_grace_s: float = 0.5,
        close_timeout_s: float = 1.0,
    ) -> None:
        self.spec, self.adapter, self.tracker = spec, adapter, tracker
        self._pipeline, self._events, self._clock = pipeline, events, clock
        self._grace, self._close_timeout = guard_grace_s, close_timeout_s
        self._caller = TimedCall(f"{spec.device_id}-io")
        self._closer = TimedCall(f"{spec.device_id}-close")
        self._link_up = False
        self._halted = False
        self._stopping = threading.Event()
        self.wake = threading.Event()

    @property
    def halted(self) -> bool:
        return self._halted

    # -- one acquisition cycle -------------------------------------------------------------
    def step(self) -> float | None:
        """Run one cycle. Returns seconds until the next cycle, or None if halted (ERROR)."""
        if self._halted or self._stopping.is_set():
            return None
        if not self._link_up:
            self._emit_change(self.tracker.begin_attempt())
            error = self._guarded("connect", self.adapter.connect, self.spec.connect_timeout_s)
            if isinstance(error, ErrorInfo):
                return self._handle_failure(error, "connect")
            self._link_up = True
            self.tracker.connect_ok()
        outcome = self._guarded("read", self.adapter.read, self.spec.response_timeout_s)
        if isinstance(outcome, ErrorInfo):
            return self._handle_failure(outcome, "read")
        received_at = self._clock.now()
        processed = self._pipeline.process(
            self.spec, outcome, received_at, self.adapter.source_kind
        )
        decision = self.tracker.record_read(processed.results, outcome.operating_report)
        self._emit_change(decision.change)
        if decision.recovered:
            self._events.record(
                EventCategory.RECOVERY,
                Severity.INFO,
                f"{self.spec.device_id}: valid data acquired again after "
                f"{decision.outage_failures} failed attempt(s).",
                device_id=self.spec.device_id,
                details={
                    "failed_attempts": decision.outage_failures,
                    "outage_seconds": decision.outage_seconds,
                },
                dedup_key="recovery",
            )
        if decision.operating_changed is not None:
            before, after = decision.operating_changed
            self._events.record(
                EventCategory.OPERATING_STATE_CHANGE,
                Severity.INFO,
                f"{self.spec.device_id}: reported operating state "
                f"{before.value if before else 'none'} -> {after.value if after else 'none'} "
                f"(source: {self.adapter.source_kind.value}).",
                device_id=self.spec.device_id,
                dedup_key=("operating", before, after),
            )
        return self.spec.poll_interval_s

    def _guarded(self, operation: str, fn: Callable[[], object], timeout_s: float):  # noqa: ANN202
        """Run an adapter call with a hard limit. Returns the value or an ErrorInfo."""
        device = self.spec.device_id
        timeout_cat = (
            ErrorCategory.CONNECTION_TIMEOUT
            if operation == "connect"
            else ErrorCategory.READ_TIMEOUT
        )
        try:
            return self._caller.run(fn, timeout_s + self._grace)
        except AdapterFailure as exc:
            return exc.error
        except CallTimeout:
            return ErrorInfo.create(
                timeout_cat, f"no response within {timeout_s:g} s", operation, device
            )
        except CallBusy:
            self.tracker.record_skipped()
            return ErrorInfo.create(
                timeout_cat,
                "previous request still in progress; cycle skipped to avoid overlapping requests",
                operation,
                device,
            )
        except Exception as exc:  # noqa: BLE001 - boundary: any adapter defect is contained here
            log.exception("Unexpected adapter error", extra={"device_id": device})
            return ErrorInfo.create(
                ErrorCategory.UNEXPECTED_ERROR,
                f"{type(exc).__name__}: {exc}",
                operation,
                device,
                retryable=False,
            )

    def _handle_failure(self, error: ErrorInfo, operation: str) -> float | None:
        self._drop_link()
        if self._stopping.is_set():  # failure caused by our own interruption during shutdown
            return None
        decision = self.tracker.record_failure(error, operation)
        self._emit_change(decision.change)
        halted = decision.delay_s is None
        category = (
            EventCategory.UNEXPECTED_ERROR
            if error.category is ErrorCategory.UNEXPECTED_ERROR
            else EventCategory.CONNECTION_FAILURE
            if operation == "connect"
            else EventCategory.ACQUISITION_FAILURE
        )
        if halted:
            outcome = (
                "retry limit reached; operator action required"
                if decision.reason == "retry_limit_reached"
                else "not retryable; operator action required"
            )
        else:
            outcome = f"retrying in {decision.delay_s:g} s"
        self._events.record(
            category,
            Severity.ERROR if halted else Severity.WARNING,
            f"{self.spec.device_id}: {error.summary()} ({outcome})",
            device_id=self.spec.device_id,
            details={
                "operation": operation,
                "error_category": error.category.value,
                "consecutive_failures": decision.failures,
                "retryable": error.retryable,
                "retry_in_s": decision.delay_s,
            },
            dedup_key=("failure", operation, error.category.value, halted),
        )
        self._halted = halted
        return decision.delay_s

    def _drop_link(self) -> None:
        """Close the adapter (bounded wait) so the next attempt starts from a fresh connection."""
        self._link_up = False
        try:
            self._closer.run(self.adapter.close, self._close_timeout)
        except Exception as exc:  # noqa: BLE001 - closing must never raise out of the worker
            log.warning("Adapter close failed: %s", exc, extra={"device_id": self.spec.device_id})

    def _emit_change(self, change: StateChange | None) -> None:
        if change is None:
            return
        self._events.record(
            EventCategory.COMMUNICATION_STATE_CHANGE,
            _STATE_SEVERITY.get(change.new, Severity.INFO),
            f"{self.spec.device_id}: communication {change.old.value} -> {change.new.value}"
            f" ({change.reason})",
            device_id=self.spec.device_id,
            details={"from": change.old.value, "to": change.new.value},
            dedup_key=("state", change.old, change.new),
        )

    # -- operator / shutdown ---------------------------------------------------------------
    def request_retry(self) -> bool:
        """Operator retry after ERROR (not for configuration errors, which need a restart)."""
        if not self._halted:
            return False
        self._emit_change(self.tracker.operator_retry())
        self._halted = False
        self.wake.set()
        return True

    def interrupt(self) -> None:
        """Begin shutdown: stop new cycles and close the adapter to unblock a pending call."""
        self._stopping.set()
        self._drop_link()
        self.wake.set()

    def finalize(self) -> None:
        """Complete shutdown after the worker thread has ended (or been abandoned)."""
        self._emit_change(self.tracker.stop())


@dataclass(frozen=True)
class ShutdownReport:
    stopped_cleanly: bool
    threads_still_running: tuple[str, ...]


class AcquisitionManager:
    def __init__(
        self,
        config: AppConfig,
        pipeline: MeasurementPipeline,
        events: EventService,
        clock: Clock | None = None,
        adapter_factory: AdapterFactory = create_adapter,
        *,
        guard_grace_s: float = 0.5,
    ) -> None:
        self._config = config
        self._pipeline, self._events = pipeline, events
        self._clock = clock or SystemClock()
        self._factory = adapter_factory
        self._grace = guard_grace_s
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = False
        self._shut_down = False
        self.workers: dict[str, DeviceWorker] = {}
        self.trackers: dict[str, DeviceStateTracker] = {}
        self._threads: dict[str, threading.Thread] = {}

    # -- lifecycle -------------------------------------------------------------------------
    def register_devices(self) -> None:
        """Create trackers for all devices and adapters for enabled ones (no I/O)."""
        for spec in self._config.devices:
            if spec.device_id in self.trackers:
                continue
            kind = self._source_kind(spec)
            tracker = DeviceStateTracker(spec, kind, self._clock)
            self.trackers[spec.device_id] = tracker
            if not spec.enabled:
                continue  # disabled devices are never polled and get no adapter
            try:
                adapter = self._factory(spec, self._clock)
                adapter.open()
            except Exception as exc:  # noqa: BLE001 - one bad device must not stop the others
                log.exception("Adapter initialisation failed", extra={"device_id": spec.device_id})
                error = ErrorInfo.create(
                    ErrorCategory.CONFIGURATION_ERROR,
                    str(exc),
                    "open",
                    spec.device_id,
                    retryable=False,
                )
                tracker.record_failure(error, "open")
                self._events.record(
                    EventCategory.ADAPTER_INIT_FAILURE,
                    Severity.ERROR,
                    f"{spec.device_id}: adapter could not be initialised: {exc}",
                    device_id=spec.device_id,
                )
                continue
            self.workers[spec.device_id] = DeviceWorker(
                spec,
                adapter,
                tracker,
                self._pipeline,
                self._events,
                self._clock,
                guard_grace_s=self._grace,
            )

    def _source_kind(self, spec: DeviceSpec):  # noqa: ANN202
        from textile_automation.adapters.registry import ADAPTERS

        return ADAPTERS[spec.adapter_type].source_kind

    def start(self) -> None:
        """Start one thread per worker. Idempotent: repeated calls never create duplicates."""
        with self._lock:
            if self._started or self._shut_down:
                return
            self.register_devices()
            self._started = True
            for device_id, worker in self.workers.items():
                thread = threading.Thread(
                    target=self._run, args=(worker,), name=f"acq-{device_id}", daemon=True
                )
                self._threads[device_id] = thread
                thread.start()

    def _run(self, worker: DeviceWorker) -> None:
        device = worker.spec.device_id
        while not self._stop.is_set():
            began = time.monotonic()
            try:
                delay = worker.step()
            except Exception as exc:  # noqa: BLE001 - last-resort boundary for this device only
                log.exception("Unhandled worker error", extra={"device_id": device})
                err = ErrorInfo.create(
                    ErrorCategory.UNEXPECTED_ERROR,
                    f"{type(exc).__name__}: {exc}",
                    "step",
                    device,
                    retryable=False,
                )
                worker.tracker.record_failure(err, "read")
                self._events.record(
                    EventCategory.UNEXPECTED_ERROR,
                    Severity.ERROR,
                    f"{device}: unexpected error: {exc}",
                    device_id=device,
                )
                worker._halted = True  # noqa: SLF001
                delay = None
            if delay is None:
                worker.wake.wait()
                worker.wake.clear()
                continue
            elapsed = time.monotonic() - began
            if elapsed > delay > 0:
                worker.tracker.record_skipped(int(elapsed // delay))
            self._stop.wait(max(0.0, delay - elapsed))

    def shutdown(self, timeout_s: float = 5.0) -> ShutdownReport:
        """Stop scheduling, wake workers, join within `timeout_s`, close adapters. Idempotent."""
        with self._lock:
            if self._shut_down:
                return ShutdownReport(True, ())
            self._shut_down = True
        self._stop.set()
        for worker in self.workers.values():
            worker.interrupt()  # close adapters FIRST so blocked reads/connects are released
        deadline = time.monotonic() + timeout_s
        for thread in self._threads.values():
            thread.join(max(0.0, deadline - time.monotonic()))
        for worker in self.workers.values():
            worker.finalize()
        for tracker in self.trackers.values():
            tracker.stop()
        alive = tuple(t.name for t in self._threads.values() if t.is_alive())
        return ShutdownReport(not alive, alive)

    # -- operator actions (application-level only; never machine control) -------------------
    def retry_device(self, device_id: str) -> bool:
        worker = self.workers.get(device_id)
        return worker.request_retry() if worker else False

    def inject_simulator_fault(self, device_id: str, **fault: object) -> object:
        worker = self.workers.get(device_id)
        if worker is None:
            raise OperationNotPermitted(f"Device '{device_id}' is not running.")
        if not worker.adapter.supports_fault_injection:
            raise OperationNotPermitted(
                f"Device '{device_id}' is not a simulated device; fault injection is refused."
            )
        spec = worker.adapter.inject_fault(**fault)
        self._events.record(
            EventCategory.SIMULATOR_CONTROL,
            Severity.INFO,
            f"{device_id}: synthetic fault scheduled in the simulator: {fault}",
            device_id=device_id,
            dedup_key=None,
        )
        return spec

    def snapshots(self) -> list[DeviceSnapshot]:
        return [
            self.trackers[s.device_id].snapshot()
            for s in self._config.devices
            if s.device_id in self.trackers
        ]

    @property
    def thread_names(self) -> list[str]:
        return [t.name for t in self._threads.values() if t.is_alive()]

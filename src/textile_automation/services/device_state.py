"""Per-device runtime state: communication state, measurement states, counters.

The tracker owns all state transitions (via domain.states.next_comm_state) and produces
immutable snapshots for the UI, so the UI never derives status on its own.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from textile_automation.clock import Clock
from textile_automation.domain.errors import ErrorInfo
from textile_automation.domain.models import (
    DeviceSpec,
    MeasurementResult,
    OperatingReport,
)
from textile_automation.domain.quality import (
    MeasurementState,
    MeasurementView,
    Quality,
    assess_measurement,
    freshness_threshold_s,
)
from textile_automation.domain.states import (
    CommEvent,
    CommunicationState,
    OperatingState,
    SourceKind,
    next_comm_state,
)
from textile_automation.services.retry import backoff_delay


@dataclass(frozen=True)
class StateChange:
    old: CommunicationState
    new: CommunicationState
    reason: str
    at: datetime


@dataclass(frozen=True)
class FailureDecision:
    change: StateChange | None
    failures: int
    delay_s: float | None  # None => halted (ERROR); no automatic retry
    reason: str  # "retrying" | "retry_limit_reached" | "non_retryable"


@dataclass(frozen=True)
class ReadDecision:
    change: StateChange | None
    recovered: bool
    outage_failures: int
    outage_seconds: float | None
    accepted: int
    rejected: int
    missing: int
    operating_changed: tuple[OperatingState | None, OperatingState | None] | None


@dataclass(frozen=True)
class OperatingView:
    state: OperatingState | None  # None => not reported by the source
    reported_at: datetime | None
    age_s: float | None
    is_current: bool
    note: str


@dataclass(frozen=True)
class DeviceSnapshot:
    device_id: str
    name: str
    device_type: str
    adapter_type: str
    source_kind: SourceKind
    enabled: bool
    comm_state: CommunicationState
    state_since: datetime
    link_up: bool
    last_success_at: datetime | None
    last_error: ErrorInfo | None
    last_error_at: datetime | None
    consecutive_failures: int
    retry_limit: int
    next_attempt_at: datetime | None
    poll_interval_s: float
    freshness_threshold_s: float
    operating: OperatingView
    measurements: tuple[MeasurementView, ...]
    cycles_ok: int
    cycles_failed: int
    cycles_skipped: int
    accepted_total: int
    rejected_total: int
    missing_total: int
    quality_counts: dict[str, int] = field(default_factory=dict)


class DeviceStateTracker:
    def __init__(self, spec: DeviceSpec, source_kind: SourceKind, clock: Clock) -> None:
        self.spec = spec
        self.source_kind = source_kind
        self._clock = clock
        self._lock = threading.RLock()
        now = clock.now()
        self._state = (
            CommunicationState.DISABLED if not spec.enabled else CommunicationState.DISCONNECTED
        )
        self._state_since = now
        self._link_up = False
        self._has_been_healthy = False
        self._failures = 0
        self._outage_started: datetime | None = None
        self._outage_failures = 0
        self._last_success_at: datetime | None = None
        self._last_error: ErrorInfo | None = None
        self._last_error_at: datetime | None = None
        self._next_attempt_at: datetime | None = None
        self._measurements = {m.key: MeasurementState(m.key) for m in spec.measurements}
        self._operating: OperatingReport | None = None
        self._operating_at: datetime | None = None
        self._cycles_ok = self._cycles_failed = self._cycles_skipped = 0
        self._accepted = self._rejected = self._missing = 0

    # -- transitions -----------------------------------------------------------------------
    def _transition(self, event: CommEvent, reason: str) -> StateChange | None:
        new = next_comm_state(self._state, event)
        if new == self._state:
            return None
        now = self._clock.now()
        change = StateChange(self._state, new, reason, now)
        self._state, self._state_since = new, now
        return change

    @property
    def state(self) -> CommunicationState:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._failures

    def begin_attempt(self) -> StateChange | None:
        with self._lock:
            event = CommEvent.BEGIN_RECOVERY if self._has_been_healthy else CommEvent.BEGIN_CONNECT
            self._link_up = False
            self._next_attempt_at = None
            return self._transition(event, "connection attempt")

    def connect_ok(self) -> None:
        with self._lock:
            self._link_up = True
            self._transition(CommEvent.CONNECT_OK, "link established")

    def record_failure(self, error: ErrorInfo, operation: str) -> FailureDecision:
        with self._lock:
            now = self._clock.now()
            policy = self.spec.retry
            self._link_up = False
            self._failures += 1
            self._cycles_failed += 1
            self._last_error, self._last_error_at = error, now
            if self._outage_started is None:
                self._outage_started, self._outage_failures = now, 0
            self._outage_failures += 1
            if not error.retryable:
                event, reason = CommEvent.FATAL, "non_retryable"
            elif self._failures >= policy.max_consecutive_failures:
                event, reason = CommEvent.GIVE_UP, "retry_limit_reached"
            else:
                event = CommEvent.CONNECT_FAIL if operation == "connect" else CommEvent.READ_FAIL
                reason = "retrying"
            change = self._transition(event, error.summary())
            halted = self._state is CommunicationState.ERROR
            delay = None if halted else backoff_delay(policy, self._failures)
            self._next_attempt_at = None if delay is None else now + _seconds(delay)
            return FailureDecision(change, self._failures, delay, reason)

    def record_skipped(self, count: int = 1) -> None:
        with self._lock:
            self._cycles_skipped += count

    def record_read(
        self, results: tuple[MeasurementResult, ...], operating: OperatingReport | None
    ) -> ReadDecision:
        """Apply one completed read cycle. `results` covers every configured measurement."""
        with self._lock:
            now = self._clock.now()
            accepted = rejected = missing = 0
            for r in results:
                st = self._measurements.get(r.measurement_key)
                if st is None:
                    continue
                valid_state = st.last_valid
                prev_flag = st.last_valid_from_previous_session
                if r.quality is Quality.VALID:
                    accepted += 1
                    valid_state, prev_flag = r, False
                elif r.quality is Quality.MISSING:
                    missing += 1
                else:
                    rejected += 1
                self._measurements[r.measurement_key] = MeasurementState(
                    st.key, valid_state, prev_flag, r
                )
            self._accepted += accepted
            self._rejected += rejected
            self._missing += missing
            self._failures = 0  # communication succeeded; data validity is tracked separately
            self._cycles_ok += 1
            self._next_attempt_at = None
            self._has_been_healthy = True

            total = len(self.spec.measurements)
            if accepted == total and total > 0:
                event = CommEvent.READ_FULL
            elif accepted > 0:
                event = CommEvent.READ_PARTIAL
            else:
                event = CommEvent.READ_NONE
            change = self._transition(event, f"{accepted}/{total} measurements accepted")

            recovered, outage_failures, outage_s = False, 0, None
            if accepted > 0:
                self._last_success_at = now
                if self._outage_started is not None:
                    recovered = True
                    outage_failures = self._outage_failures
                    outage_s = (now - self._outage_started).total_seconds()
                    self._outage_started, self._outage_failures = None, 0

            operating_changed = None
            if operating is not None:
                before = self._operating.state if self._operating else None
                if before != operating.state:
                    operating_changed = (before, operating.state)
                self._operating, self._operating_at = operating, now
            return ReadDecision(
                change,
                recovered,
                outage_failures,
                outage_s,
                accepted,
                rejected,
                missing,
                operating_changed,
            )

    def restore_previous(self, results: list[MeasurementResult]) -> None:
        """Seed last-accepted values from storage, flagged as previous-session (never fresh)."""
        with self._lock:
            for r in results:
                if r.measurement_key in self._measurements:
                    self._measurements[r.measurement_key] = MeasurementState(
                        r.measurement_key, r, True, None
                    )

    def operator_retry(self) -> StateChange | None:
        with self._lock:
            if self._state is not CommunicationState.ERROR:
                return None
            self._failures = 0
            return self._transition(CommEvent.OPERATOR_RETRY, "operator requested retry")

    def stop(self) -> StateChange | None:
        with self._lock:
            if self._state is CommunicationState.DISABLED:
                return None
            self._link_up = False
            return self._transition(CommEvent.STOP, "monitoring stopped")

    # -- snapshot --------------------------------------------------------------------------
    def snapshot(self) -> DeviceSnapshot:
        with self._lock:
            now = self._clock.now()
            threshold = freshness_threshold_s(
                self.spec.poll_interval_s, self.spec.stale_after_intervals
            )
            views = tuple(
                assess_measurement(
                    self._measurements[m.key],
                    name=m.name,
                    unit=m.unit,
                    now=now,
                    threshold_s=threshold,
                )
                for m in self.spec.measurements
            )
            counts: dict[str, int] = {}
            for v in views:
                counts[v.quality.value] = counts.get(v.quality.value, 0) + 1
            return DeviceSnapshot(
                device_id=self.spec.device_id,
                name=self.spec.name,
                device_type=self.spec.device_type,
                adapter_type=self.spec.adapter_type,
                source_kind=self.source_kind,
                enabled=self.spec.enabled,
                comm_state=self._state,
                state_since=self._state_since,
                link_up=self._link_up,
                last_success_at=self._last_success_at,
                last_error=self._last_error,
                last_error_at=self._last_error_at,
                consecutive_failures=self._failures,
                retry_limit=self.spec.retry.max_consecutive_failures,
                next_attempt_at=self._next_attempt_at,
                poll_interval_s=self.spec.poll_interval_s,
                freshness_threshold_s=threshold,
                operating=self._operating_view(now, threshold),
                measurements=views,
                cycles_ok=self._cycles_ok,
                cycles_failed=self._cycles_failed,
                cycles_skipped=self._cycles_skipped,
                accepted_total=self._accepted,
                rejected_total=self._rejected,
                missing_total=self._missing,
                quality_counts=counts,
            )

    def _operating_view(self, now: datetime, threshold: float) -> OperatingView:
        if self._operating is None or self._operating_at is None:
            return OperatingView(None, None, None, False, "Not reported by this source.")
        age = max(0.0, (now - self._operating_at).total_seconds())
        if age > threshold:
            return OperatingView(
                None,
                self._operating_at,
                age,
                False,
                f"Unknown now. Last reported {self._operating.state.value}; this may not reflect "
                "the machine's current state (a communication gap does not mean it stopped).",
            )
        return OperatingView(
            self._operating.state, self._operating_at, age, True, "As reported by the source."
        )


def _seconds(value: float):  # noqa: ANN202 - tiny helper
    from datetime import timedelta

    return timedelta(seconds=value)

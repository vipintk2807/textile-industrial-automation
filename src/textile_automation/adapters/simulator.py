"""Deterministic software simulator implementing the common adapter contract.

ALL VALUES ARE SYNTHETIC. They are generated from the configured seed and an attempt counter,
not from wall-clock time or any real machine. Generation ranges are test assumptions, not
machine operating limits, and the simulator is not a digital twin of any textile machine.

Per-measurement parameters (measurement.source_mapping):
  float/int: base (required), amplitude=0, period_cycles=60, noise=0
  bool:      period_cycles=10, start_value=false
  str:       choices (required list of strings), period_cycles=10
Device parameters (device.adapter_settings):
  seed=0, provide_source_timestamp=true, faults=[...], operating_states=[...]
Fault kinds: connect_failure, connect_timeout (count connect attempts);
  read_timeout, read_protocol_error, link_drop, slow_response, missing, invalid_value
  (count read attempts). `start` is 1-based; `duration` defaults to 1.
"""

from __future__ import annotations

import math
import random
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, TypeGuard

from textile_automation.adapters.base import DeviceAdapter
from textile_automation.clock import Clock
from textile_automation.domain.errors import (
    AdapterFailure,
    ErrorCategory,
    ErrorInfo,
    SettingIssue,
)
from textile_automation.domain.models import (
    AdapterReadout,
    DataType,
    DeviceSpec,
    MeasurementResult,
    MeasurementSpec,
    OperatingReport,
)
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import CommunicationState, OperatingState, SourceKind

CONNECT_FAULTS = ("connect_failure", "connect_timeout")
READ_FAULTS = (
    "read_timeout",
    "read_protocol_error",
    "link_drop",
    "slow_response",
    "missing",
    "invalid_value",
)
FAULT_KINDS = CONNECT_FAULTS + READ_FAULTS
INVALID_MODES = ("nan", "inf", "text", "out_of_range")
MAX_FAULT_DURATION = 100_000


@dataclass(frozen=True)
class FaultSpec:
    kind: str
    start: int
    duration: int = 1
    key: str | None = None
    mode: str | None = None
    delay_s: float | None = None

    def covers(self, attempt: int) -> bool:
        return self.start <= attempt < self.start + self.duration


@dataclass(frozen=True)
class OperatingStep:
    from_read: int
    state: OperatingState


def _num(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def check_fault(raw: Mapping[str, Any], path: str, keys: set[str]) -> list[SettingIssue]:
    issues: list[SettingIssue] = []
    kind = raw.get("kind")
    if kind not in FAULT_KINDS:
        issues.append(SettingIssue(f"{path}.kind", f"must be one of {', '.join(FAULT_KINDS)}"))
        return issues
    start, duration = raw.get("start"), raw.get("duration", 1)
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        issues.append(SettingIssue(f"{path}.start", "must be an integer >= 1"))
    if (
        not isinstance(duration, int)
        or isinstance(duration, bool)
        or not 1 <= duration <= MAX_FAULT_DURATION
    ):
        issues.append(
            SettingIssue(f"{path}.duration", f"must be an integer 1..{MAX_FAULT_DURATION}")
        )
    if kind in ("missing", "invalid_value"):
        if raw.get("key") not in keys:
            issues.append(SettingIssue(f"{path}.key", "must name a configured measurement key"))
    if kind == "invalid_value" and raw.get("mode", "nan") not in INVALID_MODES:
        issues.append(SettingIssue(f"{path}.mode", f"must be one of {', '.join(INVALID_MODES)}"))
    if kind == "slow_response":
        delay = raw.get("delay_s")
        if not _num(delay) or delay < 0:
            issues.append(SettingIssue(f"{path}.delay_s", "must be a number >= 0"))
    allowed = {"kind", "start", "duration", "key", "mode", "delay_s"}
    for extra in sorted(set(raw) - allowed):
        issues.append(SettingIssue(f"{path}.{extra}", "unknown setting"))
    return issues


def fault_from_mapping(raw: Mapping[str, Any]) -> FaultSpec:
    return FaultSpec(
        kind=str(raw["kind"]),
        start=int(raw["start"]),
        duration=int(raw.get("duration", 1)),
        key=raw.get("key"),
        mode=raw.get("mode"),
        delay_s=float(raw["delay_s"]) if raw.get("delay_s") is not None else None,
    )


def generate_value(spec: MeasurementSpec, seed: int, attempt: int) -> object:
    """Synthetic value for read attempt `attempt` (1-based). Pure and deterministic."""
    m = spec.source_mapping
    if spec.data_type.is_numeric:
        base = float(m["base"])
        amplitude = float(m.get("amplitude", 0.0))
        period = float(m.get("period_cycles", 60))
        noise = float(m.get("noise", 0.0))
        rng = random.Random(f"{seed}|{spec.key}|{attempt}")
        value = base + amplitude * math.sin(2 * math.pi * (attempt - 1) / period)
        value += noise * rng.uniform(-1.0, 1.0)
        value = round(value, 6)
        return round(value) if spec.data_type is DataType.INT else value
    period_i = int(m.get("period_cycles", 10))
    if spec.data_type is DataType.BOOL:
        start = bool(m.get("start_value", False))
        flips = (attempt - 1) // period_i
        return start if flips % 2 == 0 else not start
    choices = list(m["choices"])
    return choices[((attempt - 1) // period_i) % len(choices)]


class SimulatorAdapter(DeviceAdapter):
    adapter_type: ClassVar[str] = "simulator"
    source_kind: ClassVar[SourceKind] = SourceKind.SIMULATOR
    min_poll_interval_s: ClassVar[float] = 0.05
    supports_fault_injection: ClassVar[bool] = True

    def __init__(
        self,
        spec: DeviceSpec,
        clock: Clock,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        super().__init__(spec, clock)
        settings = spec.adapter_settings
        self._seed = int(settings.get("seed", 0))
        self._provide_ts = bool(settings.get("provide_source_timestamp", True))
        self._faults = [fault_from_mapping(f) for f in settings.get("faults", [])]
        self._steps = sorted(
            (
                OperatingStep(int(s["from_read"]), OperatingState(s["state"]))
                for s in settings.get("operating_states", [])
            ),
            key=lambda s: s.from_read,
        )
        self._lock = threading.RLock()
        self._wake = threading.Event()  # set by close() to interrupt simulated delays
        self._sleep = sleep or (lambda s: self._wake.wait(s))
        self._connected = False
        self._opened = False
        self.connect_attempts = 0
        self.read_attempts = 0

    # -- configuration ---------------------------------------------------------------------
    @classmethod
    def validate_settings(cls, spec: DeviceSpec) -> list[SettingIssue]:
        issues: list[SettingIssue] = []
        s = spec.adapter_settings
        known = {"seed", "provide_source_timestamp", "faults", "operating_states"}
        for extra in sorted(set(s) - known):
            issues.append(SettingIssue(f"adapter_settings.{extra}", "unknown simulator setting"))
        seed = s.get("seed", 0)
        if not isinstance(seed, int) or isinstance(seed, bool):
            issues.append(SettingIssue("adapter_settings.seed", "must be an integer"))
        if not isinstance(s.get("provide_source_timestamp", True), bool):
            issues.append(
                SettingIssue("adapter_settings.provide_source_timestamp", "must be true or false")
            )
        keys = {m.key for m in spec.measurements}
        faults = s.get("faults", [])
        if not isinstance(faults, list):
            issues.append(SettingIssue("adapter_settings.faults", "must be a list of tables"))
            faults = []
        for i, raw in enumerate(faults):
            if not isinstance(raw, Mapping):
                issues.append(SettingIssue(f"adapter_settings.faults[{i}]", "must be a table"))
                continue
            issues.extend(check_fault(raw, f"adapter_settings.faults[{i}]", keys))
        steps = s.get("operating_states", [])
        if not isinstance(steps, list):
            issues.append(SettingIssue("adapter_settings.operating_states", "must be a list"))
            steps = []
        for i, raw in enumerate(steps):
            path = f"adapter_settings.operating_states[{i}]"
            if not isinstance(raw, Mapping):
                issues.append(SettingIssue(path, "must be a table"))
                continue
            fr = raw.get("from_read")
            if not isinstance(fr, int) or isinstance(fr, bool) or fr < 1:
                issues.append(SettingIssue(f"{path}.from_read", "must be an integer >= 1"))
            if raw.get("state") not in {o.value for o in OperatingState}:
                issues.append(
                    SettingIssue(f"{path}.state", "must be RUNNING, STOPPED, IDLE or FAULTED")
                )
        for i, m in enumerate(spec.measurements):
            issues.extend(cls._check_measurement(m, f"measurements[{i}]"))
        return issues

    @staticmethod
    def _check_measurement(m: MeasurementSpec, path: str) -> list[SettingIssue]:
        issues: list[SettingIssue] = []
        sm = m.source_mapping
        p = f"{path}.source_mapping"
        known: set[str]
        if m.data_type.is_numeric:
            known = {"base", "amplitude", "period_cycles", "noise"}
            for k in ("base", "amplitude", "noise"):
                if k in sm and not _num(sm[k]):
                    issues.append(SettingIssue(f"{p}.{k}", "must be a finite number"))
            if "base" not in sm:
                issues.append(SettingIssue(f"{p}.base", "is required for numeric simulated values"))
            if "period_cycles" in sm and not (
                _num(sm["period_cycles"]) and sm["period_cycles"] > 0
            ):
                issues.append(SettingIssue(f"{p}.period_cycles", "must be a number > 0"))
            if all(_num(sm.get(k, 0)) for k in ("base", "amplitude", "noise")) and "base" in sm:
                spread = abs(sm.get("amplitude", 0)) + abs(sm.get("noise", 0))
                lo, hi = sm["base"] - spread, sm["base"] + spread
                if m.min_value is not None and lo < m.min_value:
                    issues.append(
                        SettingIssue(
                            p,
                            "synthetic range falls below min_value; "
                            "readings would be rejected as invalid",
                        )
                    )
                if m.max_value is not None and hi > m.max_value:
                    issues.append(
                        SettingIssue(
                            p,
                            "synthetic range exceeds max_value; "
                            "readings would be rejected as invalid",
                        )
                    )
        elif m.data_type is DataType.BOOL:
            known = {"period_cycles", "start_value"}
            pc = sm.get("period_cycles", 10)
            if not isinstance(pc, int) or isinstance(pc, bool) or pc < 1:
                issues.append(SettingIssue(f"{p}.period_cycles", "must be an integer >= 1"))
            if not isinstance(sm.get("start_value", False), bool):
                issues.append(SettingIssue(f"{p}.start_value", "must be true or false"))
        else:
            known = {"choices", "period_cycles"}
            choices = sm.get("choices")
            if (
                not isinstance(choices, list)
                or not choices
                or not all(isinstance(c, str) for c in choices)
            ):
                issues.append(SettingIssue(f"{p}.choices", "must be a non-empty list of strings"))
            pc = sm.get("period_cycles", 10)
            if not isinstance(pc, int) or isinstance(pc, bool) or pc < 1:
                issues.append(SettingIssue(f"{p}.period_cycles", "must be an integer >= 1"))
        for extra in sorted(set(sm) - known):
            issues.append(SettingIssue(f"{p}.{extra}", "unknown simulator parameter"))
        return issues

    # -- lifecycle -------------------------------------------------------------------------
    def open(self) -> None:
        self._opened = True

    @property
    def connection_state(self) -> CommunicationState:
        return CommunicationState.CONNECTED if self._connected else CommunicationState.DISCONNECTED

    def _fail(self, category: ErrorCategory, message: str, op: str) -> AdapterFailure:
        return AdapterFailure(ErrorInfo.create(category, message, op, self.spec.device_id))

    def connect(self) -> None:
        with self._lock:
            if not self._opened:
                raise AdapterFailure(
                    ErrorInfo.create(
                        ErrorCategory.RESOURCE_ERROR,
                        "adapter not opened",
                        "connect",
                        self.spec.device_id,
                    )
                )
            self.connect_attempts += 1
            n = self.connect_attempts
            self._wake.clear()
            for f in self._faults:
                if f.kind in CONNECT_FAULTS and f.covers(n):
                    if f.kind == "connect_timeout":
                        self._sleep(self.spec.connect_timeout_s)
                        raise self._fail(
                            ErrorCategory.CONNECTION_TIMEOUT,
                            "simulated connection timeout",
                            "connect",
                        )
                    raise self._fail(
                        ErrorCategory.CONNECTION_FAILURE, "simulated connection refused", "connect"
                    )
            self._connected = True

    def close(self) -> None:
        self._connected = False
        self._wake.set()

    # -- reading ---------------------------------------------------------------------------
    def read(self) -> AdapterReadout:
        with self._lock:
            if not self._connected:
                raise self._fail(ErrorCategory.CONNECTION_FAILURE, "not connected", "read")
            self.read_attempts += 1
            n = self.read_attempts
            active = [f for f in self._faults if f.kind in READ_FAULTS and f.covers(n)]
            for f in active:
                self._apply_read_fault(f)
            now = self.clock.now()
            results: list[MeasurementResult] = []
            for m in self.spec.measurements:
                if any(f.kind == "missing" and f.key == m.key for f in active):
                    continue
                value = generate_value(m, self._seed, n)
                inv = next(
                    (f for f in active if f.kind == "invalid_value" and f.key == m.key), None
                )
                if inv is not None:
                    value = self._corrupt(m, inv.mode or "nan")
                results.append(
                    MeasurementResult(
                        device_id=self.spec.device_id,
                        measurement_key=m.key,
                        value=value,
                        unit=m.unit,
                        received_at=now,
                        source_kind=self.source_kind,
                        quality=Quality.UNKNOWN,
                        source_timestamp=now if self._provide_ts else None,
                    )
                )
            return AdapterReadout(self.spec.device_id, tuple(results), self._operating(n))

    def _apply_read_fault(self, f: FaultSpec) -> None:
        timeout = self.spec.response_timeout_s
        if f.kind == "link_drop":
            self._connected = False
            raise self._fail(ErrorCategory.CONNECTION_FAILURE, "simulated link drop", "read")
        if f.kind == "read_protocol_error":
            raise self._fail(
                ErrorCategory.PROTOCOL_EXCEPTION, "simulated protocol exception response", "read"
            )
        delay = timeout if f.kind == "read_timeout" else float(f.delay_s or 0.0)
        if delay > 0 and self._sleep(min(delay, timeout)):
            raise self._fail(ErrorCategory.RESOURCE_ERROR, "operation cancelled by close()", "read")
        if f.kind == "read_timeout" or delay >= timeout:
            raise self._fail(ErrorCategory.READ_TIMEOUT, "simulated read timeout", "read")

    @staticmethod
    def _corrupt(m: MeasurementSpec, mode: str) -> object:
        if mode == "text":
            return "not-a-number"
        if mode == "inf":
            return math.inf
        if mode == "out_of_range":
            top = m.max_value if m.max_value is not None else 1.0e6
            return top + abs(top) + 1.0
        return math.nan

    def _operating(self, n: int) -> OperatingReport | None:
        current = None
        for step in self._steps:
            if step.from_read <= n:
                current = step
        if current is None:
            return None
        return OperatingReport(current.state, raw_value=f"synthetic:{current.state.value}")

    # -- controlled fault injection (simulator only) ---------------------------------------
    def inject_fault(self, **kwargs: object) -> FaultSpec:
        """Schedule a fault starting at the next relevant attempt. Raises ValueError if invalid."""
        kind = str(kwargs.get("kind"))
        with self._lock:
            counter = self.connect_attempts if kind in CONNECT_FAULTS else self.read_attempts
            raw = {**kwargs, "start": counter + 1}
            issues = check_fault(raw, "fault", {m.key for m in self.spec.measurements})
            if issues:
                raise ValueError("; ".join(str(i) for i in issues))
            fault = fault_from_mapping(raw)
            self._faults.append(fault)
            return fault

    def clear_injected_faults(self) -> None:
        with self._lock:
            configured = self.spec.adapter_settings.get("faults", [])
            self._faults = [fault_from_mapping(f) for f in configured]

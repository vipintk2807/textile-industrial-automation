"""Measurement quality model and freshness assessment.

Quality is decided here, once, in the service layer; the UI only displays it.

Precedence used by `assess_measurement` (documented in docs/ARCHITECTURE.md):
  1. Nothing ever received                         -> UNKNOWN
  2. Latest attempt INVALID/MISSING and still fresh -> that quality
     (or, if no value was ever accepted, regardless of age)
  3. Last accepted value from a previous session    -> STALE
  4. Last accepted value older than the threshold   -> STALE
  5. Otherwise                                      -> VALID

A numeric zero is a real value and is never confused with MISSING.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textile_automation.domain.errors import ErrorInfo
    from textile_automation.domain.models import MeasurementResult, Scalar
    from textile_automation.domain.states import SourceKind


class Quality(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


def freshness_threshold_s(poll_interval_s: float, stale_after_intervals: float) -> float:
    """Freshness limit derived from the device's own acquisition interval (never universal)."""
    return poll_interval_s * stale_after_intervals


@dataclass(frozen=True)
class MeasurementState:
    """What the application currently knows about one measurement."""

    key: str
    last_valid: MeasurementResult | None = None
    last_valid_from_previous_session: bool = False
    latest_attempt: MeasurementResult | None = None


@dataclass(frozen=True)
class MeasurementView:
    """Presentation-ready assessment of one measurement at a given moment."""

    key: str
    name: str
    unit: str | None
    quality: Quality
    value: Scalar | None  # last ACCEPTED value (None if none was ever accepted)
    age_s: float | None  # age of the last accepted value
    last_valid_received_at: datetime | None
    source_timestamp: datetime | None
    source_kind: SourceKind | None
    is_current: bool
    from_previous_session: bool
    note: str | None
    error: ErrorInfo | None


def _age(now: datetime, then: datetime) -> float:
    return max(0.0, (now - then).total_seconds())


def assess_measurement(
    state: MeasurementState,
    *,
    name: str,
    unit: str | None,
    now: datetime,
    threshold_s: float,
) -> MeasurementView:
    Q = Quality

    lv = state.last_valid
    latest = state.latest_attempt
    value = lv.value if lv is not None else None
    age = _age(now, lv.received_at) if lv is not None else None
    common = {
        "key": state.key,
        "name": name,
        "unit": unit,
        "value": value,  # type: ignore[arg-type]
        "age_s": age,
        "last_valid_received_at": lv.received_at if lv else None,
        "source_timestamp": lv.source_timestamp if lv else None,
        "source_kind": lv.source_kind if lv else (latest.source_kind if latest else None),
        "from_previous_session": state.last_valid_from_previous_session,
    }

    if lv is None and latest is None:
        return MeasurementView(
            quality=Q.UNKNOWN,
            is_current=False,
            note="No measurement has been received yet.",
            error=None,
            **common,  # type: ignore[arg-type]
        )

    if latest is not None and latest.quality in (Q.INVALID, Q.MISSING):
        latest_age = _age(now, latest.received_at)
        if lv is None or latest_age <= threshold_s:
            note = latest.error.summary() if latest.error else None
            return MeasurementView(
                quality=latest.quality,
                is_current=False,
                note=note,
                error=latest.error,
                **common,  # type: ignore[arg-type]
            )

    assert lv is not None and age is not None  # narrowed: lv exists on every remaining path
    if state.last_valid_from_previous_session:
        return MeasurementView(
            quality=Q.STALE,
            is_current=False,
            note="Value restored from a previous session; not yet re-acquired.",
            error=None,
            **common,  # type: ignore[arg-type]
        )
    if age > threshold_s:
        return MeasurementView(
            quality=Q.STALE,
            is_current=False,
            note=f"Last accepted value is older than the freshness limit ({threshold_s:g} s).",
            error=None,
            **common,  # type: ignore[arg-type]
        )
    return MeasurementView(
        quality=Q.VALID,
        is_current=True,
        note=None,
        error=None,
        **common,  # type: ignore[arg-type]
    )

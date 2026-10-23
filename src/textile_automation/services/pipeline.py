"""Measurement pipeline: normalise -> validate -> persist accepted -> report rejected.

Every adapter result flows through here; there is no simulator shortcut.
Only VALID measurements are written to history. Rejected or missing readings never overwrite
the last accepted value; they are reported as events and reflected in the quality state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from textile_automation.domain.errors import ErrorCategory, ErrorInfo
from textile_automation.domain.models import (
    AdapterReadout,
    DeviceSpec,
    EventCategory,
    MeasurementResult,
    Severity,
)
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import SourceKind
from textile_automation.services.events import EventService
from textile_automation.services.persistence import PersistenceGate
from textile_automation.services.validation import missing_result, safe_repr, validate_result


@dataclass(frozen=True)
class PipelineOutcome:
    results: tuple[MeasurementResult, ...]  # one per configured measurement, final quality
    accepted: int
    rejected: int
    missing: int
    persisted: bool  # True only if accepted results were really stored (or none to store)


class MeasurementPipeline:
    def __init__(self, gate: PersistenceGate, events: EventService) -> None:
        self._gate = gate
        self._events = events

    def process(
        self,
        spec: DeviceSpec,
        readout: AdapterReadout,
        received_at: datetime,
        source_kind: SourceKind,
    ) -> PipelineOutcome:
        by_key: dict[str, MeasurementResult] = {}
        duplicates: set[str] = set()
        mismatch = readout.device_id != spec.device_id
        for r in readout.results:
            if r.measurement_key in by_key:
                duplicates.add(r.measurement_key)
            else:
                by_key[r.measurement_key] = r
        known = {m.key for m in spec.measurements}
        for key in sorted(set(by_key) - known):
            self._reject_event(
                spec,
                key,
                ErrorInfo.create(
                    ErrorCategory.UNKNOWN_MEASUREMENT,
                    f"Adapter returned unconfigured measurement '{key}'; ignored.",
                    "validate",
                    spec.device_id,
                    measurement_key=key,
                ),
            )

        final: list[MeasurementResult] = []
        for m in spec.measurements:
            template = MeasurementResult(
                spec.device_id, m.key, None, m.unit, received_at, source_kind, Quality.UNKNOWN
            )
            raw = by_key.get(m.key)
            if mismatch:
                result = self._invalid(
                    template,
                    m.unit,
                    ErrorCategory.DEVICE_MISMATCH,
                    f"Readout is for device '{readout.device_id}'.",
                )
            elif m.key in duplicates:
                result = self._invalid(
                    template,
                    m.unit,
                    ErrorCategory.DUPLICATE_RESULT,
                    "Adapter returned this measurement more than once.",
                )
            elif raw is None:
                result = missing_result(m, template)
            else:
                # The application, not the adapter, owns reception time and device identity.
                raw = replace(
                    raw, device_id=spec.device_id, received_at=received_at, source_kind=source_kind
                )
                result = validate_result(m, raw)
            if result.quality is not Quality.VALID and result.error is not None:
                self._reject_event(spec, m.key, result.error, result.raw_value)
            final.append(result)

        valid = [r for r in final if r.quality is Quality.VALID]
        persisted = self._gate.write(valid) if valid else True
        return PipelineOutcome(
            tuple(final),
            accepted=len(valid),
            rejected=sum(1 for r in final if r.quality is Quality.INVALID),
            missing=sum(1 for r in final if r.quality is Quality.MISSING),
            persisted=persisted,
        )

    @staticmethod
    def _invalid(
        template: MeasurementResult, unit: str | None, category: ErrorCategory, message: str
    ) -> MeasurementResult:
        err = ErrorInfo.create(
            category,
            message,
            "validate",
            template.device_id,
            measurement_key=template.measurement_key,
        )
        return replace(template, quality=Quality.INVALID, unit=unit, error=err)

    def _reject_event(
        self, spec: DeviceSpec, key: str, error: ErrorInfo, raw: object | None = None
    ) -> None:
        self._events.record(
            EventCategory.MEASUREMENT_REJECTED,
            Severity.WARNING,
            f"{spec.device_id}/{key}: {error.summary()}",
            device_id=spec.device_id,
            details={
                "measurement_key": key,
                "error_category": error.category.value,
                "raw_value": safe_repr(raw) if raw is not None else None,
            },
            dedup_key=("rejected", key, error.category.value),
        )

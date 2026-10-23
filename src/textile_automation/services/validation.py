"""Measurement validation and quality assignment.

Every adapter result (simulated or physical) passes through `validate_result`. Failed values are
never converted to zero or any other plausible default: INVALID/MISSING results carry value=None.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime

from textile_automation.domain.errors import ErrorCategory, ErrorInfo
from textile_automation.domain.models import DataType, MeasurementResult, MeasurementSpec
from textile_automation.domain.quality import Quality

MAX_STR_LEN = 256
MAX_SAFE_INT = 2**53  # larger integers cannot be stored/displayed exactly as numbers
MAX_RAW_REPR = 200


def safe_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= MAX_RAW_REPR else text[: MAX_RAW_REPR - 1] + "…"


def missing_result(spec: MeasurementSpec, template: MeasurementResult) -> MeasurementResult:
    error = ErrorInfo.create(
        ErrorCategory.MISSING_VALUE,
        "Expected measurement was absent from the acquisition result.",
        "validate",
        template.device_id,
        measurement_key=spec.key,
    )
    return replace(
        template,
        value=None,
        unit=spec.unit,
        quality=Quality.MISSING,
        source_timestamp=None,
        raw_value=None,
        error=error,
    )


def _reject(
    spec: MeasurementSpec,
    result: MeasurementResult,
    category: ErrorCategory,
    message: str,
) -> MeasurementResult:
    error = ErrorInfo.create(
        category, message, "validate", result.device_id, measurement_key=spec.key
    )
    raw = result.raw_value if result.raw_value is not None else safe_repr(result.value)
    return replace(
        result, value=None, unit=spec.unit, quality=Quality.INVALID, raw_value=raw, error=error
    )


def _is_aware(ts: object) -> bool:
    return isinstance(ts, datetime) and ts.tzinfo is not None and ts.utcoffset() is not None


def validate_result(spec: MeasurementSpec, result: MeasurementResult) -> MeasurementResult:
    """Return the final (VALID / INVALID / MISSING) version of `result` for `spec`."""
    if result.quality == Quality.MISSING:
        return missing_result(spec, result) if result.error is None else result
    if result.quality == Quality.INVALID:  # adapter already rejected it: keep, never upgrade
        if result.error is not None:
            return replace(result, value=None, unit=spec.unit)
        return _reject(spec, result, ErrorCategory.INVALID_RESPONSE, "Adapter marked invalid.")

    if result.source_timestamp is not None and not _is_aware(result.source_timestamp):
        return _reject(
            spec,
            result,
            ErrorCategory.INVALID_TIMESTAMP,
            "Source timestamp must be a timezone-aware datetime.",
        )
    if result.unit != spec.unit:
        return _reject(
            spec,
            result,
            ErrorCategory.UNIT_MISMATCH,
            f"Adapter reported unit {result.unit!r} but the configured unit is {spec.unit!r}.",
        )

    value = result.value
    if value is None:
        return missing_result(spec, result)

    normalised: float | int | bool | str
    dt = spec.data_type
    if dt is DataType.BOOL:
        if not isinstance(value, bool):
            return _reject(spec, result, ErrorCategory.TYPE_MISMATCH, "Expected a boolean.")
        normalised = value
    elif dt is DataType.STR:
        if not isinstance(value, str):
            return _reject(spec, result, ErrorCategory.TYPE_MISMATCH, "Expected text.")
        if len(value) > MAX_STR_LEN:
            return _reject(
                spec, result, ErrorCategory.OUT_OF_RANGE, f"Text longer than {MAX_STR_LEN}."
            )
        normalised = value
    else:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return _reject(
                spec, result, ErrorCategory.TYPE_MISMATCH, "Expected a number, got a non-number."
            )
        if isinstance(value, float) and not math.isfinite(value):
            return _reject(
                spec, result, ErrorCategory.NON_FINITE_VALUE, "NaN and infinity are not valid."
            )
        if isinstance(value, int) and abs(value) > MAX_SAFE_INT:
            return _reject(
                spec, result, ErrorCategory.OUT_OF_RANGE, "Integer too large to store exactly."
            )
        if dt is DataType.INT:
            if isinstance(value, float):
                return _reject(
                    spec, result, ErrorCategory.TYPE_MISMATCH, "Expected an integer, got a float."
                )
            normalised = value
        else:
            normalised = float(value)
        if spec.min_value is not None and normalised < spec.min_value:
            return _reject(
                spec,
                result,
                ErrorCategory.OUT_OF_RANGE,
                f"Value is below the configured minimum {spec.min_value:g}.",
            )
        if spec.max_value is not None and normalised > spec.max_value:
            return _reject(
                spec,
                result,
                ErrorCategory.OUT_OF_RANGE,
                f"Value is above the configured maximum {spec.max_value:g}.",
            )

    return replace(result, value=normalised, unit=spec.unit, quality=Quality.VALID, error=None)

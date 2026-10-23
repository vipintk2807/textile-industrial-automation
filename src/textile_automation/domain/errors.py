"""Structured errors shared by adapters, services and storage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    # Adapter / communication categories (hardware communication specification, 6.3)
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    CONNECTION_TIMEOUT = "CONNECTION_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    PROTOCOL_EXCEPTION = "PROTOCOL_EXCEPTION"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNSUPPORTED_DATA_TYPE = "UNSUPPORTED_DATA_TYPE"
    INVALID_REGISTER_MAPPING = "INVALID_REGISTER_MAPPING"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    RESOURCE_ERROR = "RESOURCE_ERROR"
    # Measurement validation categories
    MISSING_VALUE = "MISSING_VALUE"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    UNKNOWN_MEASUREMENT = "UNKNOWN_MEASUREMENT"
    DUPLICATE_RESULT = "DUPLICATE_RESULT"
    DEVICE_MISMATCH = "DEVICE_MISMATCH"
    # Defects caught at a service boundary
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


CATEGORY_LABELS: dict[ErrorCategory, str] = {
    ErrorCategory.CONNECTION_FAILURE: "Connection failure",
    ErrorCategory.CONNECTION_TIMEOUT: "Connection timeout",
    ErrorCategory.READ_TIMEOUT: "Read timeout",
    ErrorCategory.PROTOCOL_EXCEPTION: "Protocol exception",
    ErrorCategory.INVALID_RESPONSE: "Invalid response",
    ErrorCategory.UNSUPPORTED_DATA_TYPE: "Unsupported data type",
    ErrorCategory.INVALID_REGISTER_MAPPING: "Invalid mapping",
    ErrorCategory.CONFIGURATION_ERROR: "Configuration error",
    ErrorCategory.RESOURCE_ERROR: "Resource error",
    ErrorCategory.MISSING_VALUE: "Missing value",
    ErrorCategory.TYPE_MISMATCH: "Wrong value type",
    ErrorCategory.NON_FINITE_VALUE: "Non-finite value",
    ErrorCategory.OUT_OF_RANGE: "Value out of configured range",
    ErrorCategory.UNIT_MISMATCH: "Unit mismatch",
    ErrorCategory.INVALID_TIMESTAMP: "Invalid timestamp",
    ErrorCategory.UNKNOWN_MEASUREMENT: "Unknown measurement",
    ErrorCategory.DUPLICATE_RESULT: "Duplicate result",
    ErrorCategory.DEVICE_MISMATCH: "Device identity mismatch",
    ErrorCategory.UNEXPECTED_ERROR: "Unexpected error",
}

# Documented retry policy: which communication errors may be retried with bounded backoff.
# Configuration, mapping, data-type and resource errors are never retried automatically.
RETRYABLE_BY_DEFAULT: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.CONNECTION_FAILURE,
        ErrorCategory.CONNECTION_TIMEOUT,
        ErrorCategory.READ_TIMEOUT,
        ErrorCategory.PROTOCOL_EXCEPTION,
        ErrorCategory.INVALID_RESPONSE,
    }
)


@dataclass(frozen=True)
class ErrorInfo:
    """Structured error: what failed, where, and whether an automatic retry is allowed."""

    category: ErrorCategory
    message: str
    operation: str
    device_id: str | None = None
    retryable: bool = False
    measurement_key: str | None = None

    @classmethod
    def create(
        cls,
        category: ErrorCategory,
        message: str,
        operation: str,
        device_id: str | None = None,
        *,
        measurement_key: str | None = None,
        retryable: bool | None = None,
    ) -> ErrorInfo:
        if retryable is None:
            retryable = category in RETRYABLE_BY_DEFAULT
        return cls(category, message, operation, device_id, retryable, measurement_key)

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self.category]

    def summary(self) -> str:
        return f"{self.label}: {self.message}"


class AdapterFailure(Exception):
    """Raised by adapters for connect/read failures. Carries a structured ErrorInfo."""

    def __init__(self, error: ErrorInfo) -> None:
        super().__init__(error.summary())
        self.error = error


class StorageError(Exception):
    """Raised by the storage layer. Never reported as a successful write."""


class OperationNotPermitted(Exception):
    """Raised when a caller requests something outside the read-only monitoring scope."""


@dataclass(frozen=True)
class SettingIssue:
    """A problem with a configuration value, addressed by its path (e.g. devices[0].name)."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message

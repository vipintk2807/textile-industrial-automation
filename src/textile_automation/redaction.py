"""Redaction of secret-looking settings so they never reach logs, events or the UI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.IGNORECASE
)
REDACTED = "***"


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY_PATTERN.search(key))


def redact_mapping(value: Any) -> Any:
    """Return a deep copy with values of sensitive-looking keys replaced by '***'."""
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if is_sensitive_key(str(k)) else redact_mapping(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_mapping(v) for v in value]
    return value


def find_sensitive_keys(value: Any, path: str = "") -> list[str]:
    """Paths of sensitive-looking keys inside a nested mapping (used to reject plaintext secrets)."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for k, v in value.items():
            here = f"{path}.{k}" if path else str(k)
            if is_sensitive_key(str(k)):
                found.append(here)
            else:
                found.extend(find_sensitive_keys(v, here))
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            found.extend(find_sensitive_keys(v, f"{path}[{i}]"))
    return found

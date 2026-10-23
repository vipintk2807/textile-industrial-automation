"""Common device adapter contract.

Adapters are READ-ONLY by construction: the contract has no write, command or setpoint
operation, and nothing in the application can add one without changing this file.

Thread-safety: `close()` may be called from another thread while `connect()` or `read()` is
blocked, and should interrupt that call where possible. `close()` must be idempotent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from textile_automation.clock import Clock
from textile_automation.domain.errors import OperationNotPermitted, SettingIssue
from textile_automation.domain.models import AdapterReadout, DeviceSpec
from textile_automation.domain.states import CommunicationState, SourceKind


class DeviceAdapter(ABC):
    adapter_type: ClassVar[str]
    source_kind: ClassVar[SourceKind]
    min_poll_interval_s: ClassVar[float] = 0.1
    max_poll_interval_s: ClassVar[float] = 3600.0
    supports_fault_injection: ClassVar[bool] = False

    def __init__(self, spec: DeviceSpec, clock: Clock) -> None:
        self.spec = spec
        self.clock = clock

    @classmethod
    @abstractmethod
    def validate_settings(cls, spec: DeviceSpec) -> list[SettingIssue]:
        """Validate adapter-specific settings and measurement mappings (no I/O)."""

    @abstractmethod
    def open(self) -> None:
        """Initialise local resources. Performs no network or device I/O."""

    @abstractmethod
    def connect(self) -> None:
        """Establish communication. Raises AdapterFailure on failure."""

    @abstractmethod
    def read(self) -> AdapterReadout:
        """Acquire the configured measurements. Raises AdapterFailure on failure."""

    @abstractmethod
    def close(self) -> None:
        """Drop the link and release resources. Idempotent; may be followed by connect()."""

    @property
    @abstractmethod
    def connection_state(self) -> CommunicationState:
        """Link-level state as seen by the adapter (DISCONNECTED or CONNECTED)."""

    def inject_fault(self, **kwargs: object) -> object:
        """Fault injection is available only on simulated adapters."""
        raise OperationNotPermitted(
            f"Adapter '{self.adapter_type}' does not support fault injection."
        )

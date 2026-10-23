"""Adapter registry. Only implemented adapters can be configured."""

from __future__ import annotations

from textile_automation.adapters.base import DeviceAdapter
from textile_automation.adapters.simulator import SimulatorAdapter
from textile_automation.clock import Clock
from textile_automation.domain.models import DeviceSpec

ADAPTERS: dict[str, type[DeviceAdapter]] = {SimulatorAdapter.adapter_type: SimulatorAdapter}

# Recognised but intentionally not implemented in this release (see docs/HARDWARE_INTEGRATION.md).
DEFERRED_ADAPTERS: dict[str, str] = {
    "modbus_tcp": "the read-only Modbus TCP adapter is deferred until a documented device "
    "mapping and a protocol test server are available",
    "modbus_rtu": "deferred (requires an identified device and serial interface)",
    "opcua": "deferred (requires a documented server endpoint)",
    "mqtt": "deferred (requires a defined message contract)",
}


def unsupported_message(adapter_type: str) -> str:
    supported = ", ".join(sorted(ADAPTERS))
    if adapter_type in DEFERRED_ADAPTERS:
        return (
            f"adapter '{adapter_type}' is not available in this release: "
            f"{DEFERRED_ADAPTERS[adapter_type]}. Supported adapters: {supported}"
        )
    return f"unsupported adapter '{adapter_type}'. Supported adapters: {supported}"


def create_adapter(spec: DeviceSpec, clock: Clock) -> DeviceAdapter:
    try:
        cls = ADAPTERS[spec.adapter_type]
    except KeyError:
        raise ValueError(unsupported_message(spec.adapter_type)) from None
    return cls(spec, clock)

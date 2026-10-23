"""Static and structural checks that the release stays read-only and local."""

import ast
import re
import unittest
from pathlib import Path

from textile_automation.adapters.base import DeviceAdapter
from textile_automation.adapters.simulator import SimulatorAdapter
from textile_automation.domain.errors import OperationNotPermitted

SRC = Path(__file__).resolve().parents[2] / "src" / "textile_automation"
APP = Path(__file__).resolve().parents[2] / "app.py"

WRITE_NAMES = re.compile(
    r"(write_(single_)?(register|coil)s?|mask_write|readwrite|send_command|start_machine|stop_machine|"
    r"set_setpoint|setpoint|actuator|bypass_interlock|emergency_stop)",
    re.IGNORECASE,
)
FORBIDDEN_IMPORTS = {
    "pymodbus",
    "socket",
    "serial",
    "opcua",
    "asyncua",
    "paho",
    "requests",
    "urllib3",
    "httpx",
    "ftplib",
}


def py_files():
    return [*SRC.rglob("*.py"), APP]


class ReadOnlyTests(unittest.TestCase):
    def test_no_control_or_write_operations_anywhere_in_source(self):
        offenders = []
        for f in py_files():
            tree = ast.parse(f.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                name = (
                    node.name
                    if isinstance(node, ast.FunctionDef | ast.ClassDef)
                    else node.attr
                    if isinstance(node, ast.Attribute)
                    else node.id
                    if isinstance(node, ast.Name)
                    else None
                )
                if name and WRITE_NAMES.search(name):
                    offenders.append(f"{f.name}:{node.lineno}:{name}")
        self.assertEqual(offenders, [])

    def test_adapter_contract_has_no_write_capability(self):
        public = {
            n
            for n, v in vars(DeviceAdapter).items()
            if not n.startswith("_")
            and (callable(v) or isinstance(v, classmethod | staticmethod | property))
        }
        self.assertEqual(
            public,
            {
                "open",
                "connect",
                "read",
                "close",
                "validate_settings",
                "inject_fault",
                "connection_state",
            },
        )
        self.assertFalse(any(WRITE_NAMES.search(n) for n in public))

    def test_fault_injection_is_simulator_only_by_contract(self):
        self.assertTrue(SimulatorAdapter.supports_fault_injection)
        self.assertFalse(DeviceAdapter.supports_fault_injection)

    def test_manager_refuses_injection_on_non_simulated_devices(self):
        from helpers import device
        from textile_automation.adapters.base import DeviceAdapter as Base
        from textile_automation.clock import ManualClock
        from textile_automation.domain.models import AppConfig
        from textile_automation.domain.states import CommunicationState, SourceKind
        from textile_automation.services.acquisition import AcquisitionManager
        from textile_automation.services.events import EventService
        from textile_automation.services.persistence import PersistenceGate
        from textile_automation.services.pipeline import MeasurementPipeline
        from helpers import FakeStorage

        class Physicalish(Base):
            adapter_type = "simulator"  # registry lookup key reused for the test
            source_kind = SourceKind.PHYSICAL

            @classmethod
            def validate_settings(cls, spec):
                return []

            def open(self): ...
            def connect(self): ...
            def read(self):
                raise NotImplementedError

            def close(self): ...
            @property
            def connection_state(self):
                return CommunicationState.DISCONNECTED

        storage, clock = FakeStorage(), ManualClock()
        events = EventService(storage, clock)
        mgr = AcquisitionManager(
            AppConfig(devices=(device(),)),
            MeasurementPipeline(PersistenceGate(storage, events, clock), events),
            events,
            clock,
            adapter_factory=lambda s, c: Physicalish(s, c),
        )
        mgr.register_devices()
        with self.assertRaises(OperationNotPermitted):
            mgr.inject_simulator_fault("dev-1", kind="read_timeout")

    def test_no_network_or_serial_libraries_imported(self):
        offenders = []
        for f in py_files():
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8-sig"))):
                mods = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                for m in mods:
                    if m.split(".")[0] in FORBIDDEN_IMPORTS:
                        offenders.append(f"{f.name}: {m}")
        self.assertEqual(
            offenders, [], "no industrial/network client may be imported in this release"
        )

    def test_ui_modules_do_not_import_adapters_or_open_connections(self):
        for f in (SRC / "ui").glob("*.py"):
            text = f.read_text(encoding="utf-8-sig")
            self.assertNotIn("textile_automation.adapters", text, f.name)
            self.assertNotIn("AcquisitionManager", text, f.name)

    def test_no_automatic_discovery_or_scanning(self):
        for f in py_files():
            text = f.read_text(encoding="utf-8-sig").lower()
            for word in (
                "portscan",
                "arp -a",
                "nmap",
                "broadcast",
                "discover_devices",
                "scan_network",
            ):
                self.assertNotIn(word, text, f.name)

    def test_server_binds_locally_and_telemetry_is_off(self):
        text = (APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8-sig")
        self.assertIn('address = "127.0.0.1"', text)
        self.assertIn("gatherUsageStats = false", text)

    def test_no_hardcoded_secrets_or_private_addresses(self):
        pattern = re.compile(
            r"(password|secret|api[_-]?key|token)\s*=\s*[\"'][^\"']+[\"']", re.IGNORECASE
        )
        for f in [
            *py_files(),
            *APP.parent.glob("config/*.toml"),
            *APP.parent.glob("examples/*.toml"),
        ]:
            text = f.read_text(encoding="utf-8-sig")
            self.assertIsNone(pattern.search(text), f.name)
            self.assertIsNone(
                re.search(r"\b(192\.168|10\.\d+|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+\b", text), f.name
            )

    def test_simulator_cannot_be_silently_replaced_by_physical(self):
        from textile_automation.adapters.registry import ADAPTERS

        self.assertTrue(all(c.source_kind.value == "SIMULATOR" for c in ADAPTERS.values()))


if __name__ == "__main__":
    unittest.main()

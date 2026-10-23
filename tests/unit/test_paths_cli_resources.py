import tempfile
import unittest
from pathlib import Path

from helpers import FakeStorage, device
from textile_automation.paths import (
    RuntimePaths,
    default_data_dir,
    resolve_config_path,
    resolve_log_level,
)
from textile_automation.services.events import RING_SIZE


class PathTests(unittest.TestCase):
    def test_windows_default_uses_localappdata(self):
        p = default_data_dir({"LOCALAPPDATA": r"C:\Users\x\AppData\Local"})
        self.assertEqual(p.name, "TextileIndustrialAutomation")
        self.assertIn("AppData", str(p))

    def test_env_override_wins(self):
        self.assertEqual(
            default_data_dir({"TEXTILE_AUTOMATION_DATA_DIR": "/tmp/tia", "LOCALAPPDATA": "x"}),
            Path("/tmp/tia"),
        )

    def test_runtime_paths_separate_data_from_source(self):
        rp = RuntimePaths.resolve(Path("/data"))
        self.assertEqual(
            (rp.database, rp.log_dir), (Path("/data/textile_automation.db"), Path("/data/logs"))
        )
        self.assertEqual(
            RuntimePaths.resolve(Path("/data"), Path("/other/x.db")).database, Path("/other/x.db")
        )

    def test_config_resolution_order(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "config").mkdir()
            self.assertEqual(resolve_config_path(None, base, {}), (None, False))
            ex = base / "config" / "devices.example.toml"
            ex.write_text("x")
            self.assertEqual(resolve_config_path(None, base, {}), (ex, True))
            real = base / "config" / "devices.toml"
            real.write_text("x")
            self.assertEqual(resolve_config_path(None, base, {}), (real, False))
            self.assertEqual(
                resolve_config_path(None, base, {"TEXTILE_AUTOMATION_CONFIG": "/e.toml"}),
                (Path("/e.toml"), False),
            )
            self.assertEqual(
                resolve_config_path(
                    Path("/x.toml"), base, {"TEXTILE_AUTOMATION_CONFIG": "/e.toml"}
                ),
                (Path("/x.toml"), False),
            )

    def test_log_level_override_is_validated(self):
        self.assertEqual(
            resolve_log_level("INFO", {"TEXTILE_AUTOMATION_LOG_LEVEL": "debug"}), "DEBUG"
        )
        self.assertEqual(
            resolve_log_level("INFO", {"TEXTILE_AUTOMATION_LOG_LEVEL": "LOUD"}), "INFO"
        )


class ResourceBoundsTests(unittest.TestCase):
    def test_memory_structures_stay_bounded_under_sustained_failure(self):
        from tests_support import run_steps  # noqa: F401  (ensures helpers import)
        from textile_automation.adapters.simulator import SimulatorAdapter
        from textile_automation.clock import ManualClock
        from textile_automation.domain.models import RetryPolicy
        from textile_automation.services.acquisition import DeviceWorker
        from textile_automation.services.device_state import DeviceStateTracker
        from textile_automation.services.events import EventService
        from textile_automation.services.persistence import PersistenceGate
        from textile_automation.services.pipeline import MeasurementPipeline

        clock, storage = ManualClock(), FakeStorage()
        events = EventService(storage, clock, dedup_window_s=60)
        spec = device(
            settings={"faults": [{"kind": "read_timeout", "start": 2, "duration": 10**6}]},
            retry=RetryPolicy(0.1, 0.1, 1.0, 1000),
        )
        adapter = SimulatorAdapter(spec, clock, sleep=lambda s: False)
        adapter.open()
        tracker = DeviceStateTracker(spec, adapter.source_kind, clock)
        worker = DeviceWorker(
            spec,
            adapter,
            tracker,
            MeasurementPipeline(PersistenceGate(storage, events, clock), events),
            events,
            clock,
        )
        for _ in range(900):
            clock.advance(worker.step() or 0)
        self.assertLessEqual(
            len(storage.events), 40, "sustained failures must not flood the event log"
        )
        self.assertLessEqual(len(events.recent(10_000)), RING_SIZE)
        self.assertEqual(len(storage.measurements), 1)
        self.assertGreater(events.suppressed_total, 100)

    def test_sustained_normal_operation_keeps_no_unbounded_in_memory_history(self):
        from tests_support import run_steps

        snap = run_steps([], 300)
        self.assertEqual(snap.cycles_ok, 300)
        self.assertEqual(len(snap.measurements), 1)  # state holds latest values only


if __name__ == "__main__":
    unittest.main()

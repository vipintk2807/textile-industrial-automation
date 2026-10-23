"""Scenario tests driven by the SHIPPED example configuration (deterministic, no sleeping).

The golden values are a regression anchor for the seeded generator (synthetic data, not machine
data): if they change, simulated output changed, and documentation/tests must be updated on purpose.
"""

import tomllib
import unittest
from pathlib import Path

from helpers import FakeStorage
from textile_automation.adapters.simulator import SimulatorAdapter, generate_value
from textile_automation.clock import ManualClock
from textile_automation.config.build import build_app_config
from textile_automation.domain.models import EventCategory as EC
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import CommunicationState as S
from textile_automation.services.acquisition import DeviceWorker
from textile_automation.services.device_state import DeviceStateTracker
from textile_automation.services.events import EventService
from textile_automation.services.persistence import PersistenceGate
from textile_automation.services.pipeline import MeasurementPipeline

ROOT = Path(__file__).resolve().parents[2]
CONFIG = build_app_config(
    tomllib.loads((ROOT / "config" / "devices.example.toml").read_text(encoding="utf-8"))
)


class GoldenSequence(unittest.TestCase):
    def test_seeded_values_are_stable(self):
        knit = CONFIG.devices[0]
        speed = [generate_value(knit.measurements[0], 42, n) for n in range(1, 7)]
        for got, want in zip(
            speed,
            [1201.592969, 1204.687714, 1207.23539, 1211.934632, 1217.611821, 1218.700945],
            strict=True,
        ):
            self.assertAlmostEqual(got, want, places=5)
        temp = [generate_value(knit.measurements[1], 42, n) for n in range(1, 4)]
        for got, want in zip(temp, [42.460587, 42.594246, 42.775501], strict=True):
            self.assertAlmostEqual(got, want, places=5)

    def test_example_values_respect_their_configured_limits(self):
        for dev in CONFIG.devices:
            for m in dev.measurements:
                if m.data_type.is_numeric:
                    for n in range(1, 400):
                        v = generate_value(m, dev.adapter_settings.get("seed", 0), n)
                        self.assertTrue(
                            (m.min_value is None or v >= m.min_value)
                            and (m.max_value is None or v <= m.max_value),
                            (m.key, n, v),
                        )


class DyeingFaultScenario(unittest.TestCase):
    """sim-dye-02: timeouts at reads 10-12, 'liquor_level' missing 30-34, NaN bath temp 45-47, link drop at 60."""

    def setUp(self):
        self.spec = CONFIG.devices[1]
        self.clock = ManualClock()
        self.storage = FakeStorage()
        self.events = EventService(self.storage, self.clock, dedup_window_s=0.0001)
        adapter = SimulatorAdapter(self.spec, self.clock, sleep=lambda s: False)
        adapter.open()
        self.adapter = adapter
        self.tracker = DeviceStateTracker(self.spec, adapter.source_kind, self.clock)
        self.worker = DeviceWorker(
            self.spec,
            adapter,
            self.tracker,
            MeasurementPipeline(
                PersistenceGate(self.storage, self.events, self.clock), self.events
            ),
            self.events,
            self.clock,
            guard_grace_s=0.05,
        )

    def run_until_read(self, n):
        timeline = []
        guard = 0
        while self.adapter.read_attempts < n and guard < 500:
            guard += 1
            delay = self.worker.step()
            self.clock.advance(delay or 0)
            timeline.append((self.adapter.read_attempts, self.tracker.state))
        return timeline

    def test_full_script_produces_expected_progression(self):
        self.run_until_read(9)
        self.assertEqual(self.tracker.state, S.CONNECTED)
        self.run_until_read(10)
        self.assertEqual(self.tracker.state, S.DEGRADED)  # first timeout
        self.run_until_read(13)
        self.run_until_read(14)
        self.assertEqual(self.tracker.state, S.CONNECTED)  # recovered with verified data
        self.assertIn(EC.RECOVERY, [e.category for e in self.events.recent(1000)])

        self.run_until_read(31)
        snap = self.tracker.snapshot()
        self.assertEqual(self.tracker.state, S.DEGRADED)  # partial data
        by_key = {m.key: m for m in snap.measurements}
        self.assertEqual(by_key["liquor_level"].quality, Quality.MISSING)
        self.assertEqual(by_key["bath_temperature"].quality, Quality.VALID)
        self.assertEqual(snap.consecutive_failures, 0)  # link fine; data issue only

        self.run_until_read(36)
        self.assertEqual(self.tracker.state, S.CONNECTED)

        self.run_until_read(46)
        by_key = {m.key: m for m in self.tracker.snapshot().measurements}
        self.assertEqual(by_key["bath_temperature"].quality, Quality.INVALID)
        self.assertIsNotNone(
            by_key["bath_temperature"].value
        )  # last accepted value retained, labelled
        self.assertEqual(by_key["bath_temperature"].error.category.value, "NON_FINITE_VALUE")

        self.run_until_read(49)
        self.assertEqual(self.tracker.state, S.CONNECTED)

        self.run_until_read(60)
        self.assertEqual(self.tracker.state, S.DEGRADED)  # link drop
        self.run_until_read(62)
        self.assertEqual(self.tracker.state, S.CONNECTED)
        # initial connect + one reconnect after each failed read (reads 10, 11, 12) + one after the link drop
        self.assertEqual(self.adapter.connect_attempts, 1 + 3 + 1)

    def test_nothing_invalid_or_missing_was_ever_stored(self):
        self.run_until_read(70)
        self.assertTrue(self.storage.measurements)
        self.assertTrue(
            all(
                m.quality is Quality.VALID and m.value is not None
                for m in self.storage.measurements
            )
        )
        self.assertTrue(all(m.source_kind.value == "SIMULATOR" for m in self.storage.measurements))
        rejected = [e for e in self.events.recent(1000) if e.category == EC.MEASUREMENT_REJECTED]
        self.assertTrue(rejected)

    def test_disabled_example_device_is_never_polled(self):
        disabled = CONFIG.devices[2]
        self.assertFalse(disabled.enabled)
        self.assertEqual(
            DeviceStateTracker(disabled, SimulatorAdapter.source_kind, self.clock).state, S.DISABLED
        )


if __name__ == "__main__":
    unittest.main()

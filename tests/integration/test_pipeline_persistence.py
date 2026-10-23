import math
import unittest

from helpers import FakeStorage, device, meas
from textile_automation.clock import ManualClock
from textile_automation.domain.errors import ErrorCategory as C
from textile_automation.domain.models import AdapterReadout, EventCategory as EC, MeasurementResult
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import SourceKind
from textile_automation.services.events import EventService
from textile_automation.services.persistence import PersistenceGate, PersistenceMode
from textile_automation.services.pipeline import MeasurementPipeline


def raw(key, value, unit="rpm", device_id="dev-1"):
    clock = ManualClock()
    return MeasurementResult(
        device_id, key, value, unit, clock.now(), SourceKind.SIMULATOR, Quality.UNKNOWN
    )


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.storage = FakeStorage()
        self.events = EventService(self.storage, self.clock)
        self.gate = PersistenceGate(self.storage, self.events, self.clock)
        self.pipe = MeasurementPipeline(self.gate, self.events)
        self.spec = device(measurements=[meas("a"), meas("b", max_value=10)])

    def run_pipe(self, results, device_id="dev-1"):
        return self.pipe.process(
            self.spec,
            AdapterReadout(device_id, tuple(results)),
            self.clock.now(),
            SourceKind.SIMULATOR,
        )

    def test_valid_results_are_persisted_with_full_metadata(self):
        out = self.run_pipe([raw("a", 1.0), raw("b", 2.0)])
        self.assertEqual((out.accepted, out.rejected, out.missing, out.persisted), (2, 0, 0, True))
        stored = self.storage.measurements[0]
        self.assertEqual(
            (stored.device_id, stored.unit, stored.quality, stored.source_kind),
            ("dev-1", "rpm", Quality.VALID, SourceKind.SIMULATOR),
        )
        self.assertEqual(stored.received_at, self.clock.now())

    def test_invalid_not_persisted_and_reported(self):
        out = self.run_pipe([raw("a", 1.0), raw("b", 99.0)])
        self.assertEqual((out.accepted, out.rejected), (1, 1))
        self.assertEqual([m.measurement_key for m in self.storage.measurements], ["a"])
        ev = [e for e in self.storage.events if e.category == EC.MEASUREMENT_REJECTED]
        self.assertEqual(len(ev), 1)
        self.assertIn("b", ev[0].message)

    def test_missing_key_is_missing_not_zero(self):
        out = self.run_pipe([raw("a", 1.0)])
        b = next(r for r in out.results if r.measurement_key == "b")
        self.assertEqual((b.quality, b.value), (Quality.MISSING, None))
        self.assertEqual(len(self.storage.measurements), 1)

    def test_nan_rejected(self):
        out = self.run_pipe([raw("a", math.nan), raw("b", 1.0)])
        self.assertEqual(out.results[0].error.category, C.NON_FINITE_VALUE)

    def test_unknown_key_ignored_but_reported(self):
        out = self.run_pipe([raw("a", 1.0), raw("b", 1.0), raw("zzz", 5.0)])
        self.assertEqual(len(out.results), 2)
        self.assertTrue(any("zzz" in e.message for e in self.storage.events))
        self.assertTrue(all(m.measurement_key != "zzz" for m in self.storage.measurements))

    def test_duplicate_result_rejected(self):
        out = self.run_pipe([raw("a", 1.0), raw("a", 2.0), raw("b", 1.0)])
        a = out.results[0]
        self.assertEqual(a.error.category, C.DUPLICATE_RESULT)

    def test_device_mismatch_rejects_everything(self):
        out = self.run_pipe([raw("a", 1.0), raw("b", 1.0)], device_id="other")
        self.assertEqual(out.accepted, 0)
        self.assertTrue(all(r.error.category == C.DEVICE_MISMATCH for r in out.results))
        self.assertEqual(self.storage.measurements, [])

    def test_application_owns_device_id_and_received_at(self):
        later = self.clock.now()
        r = raw("a", 1.0)
        self.clock.advance(5)
        out = self.pipe.process(
            self.spec,
            AdapterReadout("dev-1", (r, raw("b", 1.0))),
            self.clock.now(),
            SourceKind.SIMULATOR,
        )
        self.assertEqual(out.results[0].received_at, self.clock.now())
        self.assertNotEqual(out.results[0].received_at, later)


class PersistenceFailureTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.storage = FakeStorage()
        self.events = EventService(self.storage, self.clock)
        self.gate = PersistenceGate(self.storage, self.events, self.clock)
        self.pipe = MeasurementPipeline(self.gate, self.events)
        self.spec = device(measurements=[meas("a")])

    def cycle(self):
        self.clock.advance(1)
        return self.pipe.process(
            self.spec,
            AdapterReadout("dev-1", (raw("a", 1.0),)),
            self.clock.now(),
            SourceKind.SIMULATOR,
        )

    def test_failed_write_is_not_reported_as_success(self):
        self.storage.fail = True
        out = self.cycle()
        self.assertFalse(out.persisted)
        self.assertEqual(out.accepted, 1)  # live monitoring continues
        st = self.gate.status()
        self.assertEqual(
            (st.mode, st.dropped_measurements, st.writes_failed), (PersistenceMode.RESTRICTED, 1, 1)
        )
        self.assertEqual(self.storage.measurements, [])

    def test_no_unbounded_backlog_and_no_duplicates_after_recovery(self):
        self.storage.fail = True
        for _ in range(50):
            self.cycle()
        self.assertEqual(self.gate.status().dropped_measurements, 50)
        self.storage.fail = False
        self.cycle()
        self.assertEqual(len(self.storage.measurements), 1)  # only the new one; nothing replayed
        st = self.gate.status()
        self.assertEqual(st.mode, PersistenceMode.NORMAL)

    def test_restricted_mode_event_and_restore_event(self):
        self.storage.fail = True
        self.cycle()
        self.cycle()
        self.storage.fail = False
        self.cycle()
        cats = [e.category for e in self.events.recent()]
        self.assertIn(EC.PERSISTENCE_ERROR, cats)
        self.assertIn(EC.PERSISTENCE_RESTORED, cats)
        restored = next(e for e in self.events.recent() if e.category == EC.PERSISTENCE_RESTORED)
        self.assertEqual(restored.details["dropped_measurements"], 2)

    def test_empty_batch_is_trivially_ok(self):
        self.assertTrue(self.gate.write([]))
        self.assertEqual(self.storage.save_calls, 0)


if __name__ == "__main__":
    unittest.main()

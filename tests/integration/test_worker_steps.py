"""Deterministic, step-driven worker tests (no threads, no sleeping)."""

import threading
import unittest

from helpers import FakeStorage, device, meas
from textile_automation.adapters.base import DeviceAdapter
from textile_automation.adapters.simulator import SimulatorAdapter
from textile_automation.clock import ManualClock
from textile_automation.domain.errors import AdapterFailure, ErrorCategory as C, ErrorInfo
from textile_automation.domain.models import AdapterReadout, EventCategory as EC, RetryPolicy
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import CommunicationState as S, SourceKind
from textile_automation.services.acquisition import DeviceWorker
from textile_automation.services.device_state import DeviceStateTracker
from textile_automation.services.events import EventService
from textile_automation.services.persistence import PersistenceGate
from textile_automation.services.pipeline import MeasurementPipeline

POLICY = RetryPolicy(
    initial_delay_s=1.0, max_delay_s=4.0, multiplier=2.0, max_consecutive_failures=4
)


class Rig:
    def __init__(self, spec=None, adapter=None, grace=0.05):
        self.clock = ManualClock()
        self.storage = FakeStorage()
        self.events = EventService(self.storage, self.clock, dedup_window_s=0.0001)
        gate = PersistenceGate(self.storage, self.events, self.clock)
        self.spec = spec or device(retry=POLICY)
        self.adapter = adapter or SimulatorAdapter(self.spec, self.clock, sleep=lambda s: False)
        self.adapter.open()
        self.tracker = DeviceStateTracker(self.spec, self.adapter.source_kind, self.clock)
        self.worker = DeviceWorker(
            self.spec,
            self.adapter,
            self.tracker,
            MeasurementPipeline(gate, self.events),
            self.events,
            self.clock,
            guard_grace_s=grace,
            close_timeout_s=0.2,
        )

    def step(self, advance=True):
        delay = self.worker.step()
        if advance and delay:
            self.clock.advance(delay)
        return delay

    @property
    def state(self):
        return self.tracker.state

    def categories(self):
        return [e.category for e in self.events.recent(1000)]


def sim_rig(faults=None, **kw):
    spec = device(settings={"faults": faults or []}, retry=POLICY, **kw)
    return Rig(spec)


class NormalOperation(unittest.TestCase):
    def test_first_cycle_connects_verifies_data_and_persists(self):
        r = sim_rig()
        self.assertEqual(r.step(), 1.0)
        self.assertEqual(r.state, S.CONNECTED)
        self.assertEqual(len(r.storage.measurements), 1)
        snap = r.tracker.snapshot()
        self.assertEqual(snap.measurements[0].quality, Quality.VALID)
        self.assertEqual(snap.source_kind, SourceKind.SIMULATOR)

    def test_disabled_device_state_is_disabled(self):
        t = DeviceStateTracker(device(enabled=False), SourceKind.SIMULATOR, ManualClock())
        self.assertEqual(t.state, S.DISABLED)
        self.assertEqual(t.snapshot().comm_state, S.DISABLED)


class ConnectFailures(unittest.TestCase):
    def test_backoff_is_bounded_then_error_and_halt(self):
        r = sim_rig([{"kind": "connect_failure", "start": 1, "duration": 100}])
        delays = [r.step() for _ in range(4)]
        self.assertEqual(delays[:3], [1.0, 2.0, 4.0])
        self.assertIsNone(delays[3])  # limit (4) reached => halted
        self.assertEqual(r.state, S.ERROR)
        self.assertIsNone(r.step())  # no further attempts
        self.assertEqual(r.adapter.connect_attempts, 4)  # exactly the bounded number
        self.assertEqual(r.storage.measurements, [])

    def test_never_connected_device_waits_as_disconnected(self):
        r = sim_rig([{"kind": "connect_failure", "start": 1, "duration": 100}])
        r.step()
        self.assertEqual(r.state, S.DISCONNECTED)
        snap = r.tracker.snapshot()
        self.assertEqual(snap.consecutive_failures, 1)
        self.assertIsNotNone(snap.next_attempt_at)

    def test_operator_retry_after_error(self):
        r = sim_rig([{"kind": "connect_failure", "start": 1, "duration": 4}])
        for _ in range(4):
            r.step()
        self.assertEqual(r.state, S.ERROR)
        self.assertFalse(Rig().worker.request_retry())  # not halted -> nothing to do
        self.assertTrue(r.worker.request_retry())
        r.step()
        self.assertEqual(r.state, S.CONNECTED)

    def test_failure_events_are_recorded_with_retry_info(self):
        r = sim_rig([{"kind": "connect_failure", "start": 1, "duration": 100}])
        r.step()
        ev = next(e for e in r.events.recent() if e.category == EC.CONNECTION_FAILURE)
        self.assertIn("retrying in 1 s", ev.message)
        self.assertEqual(ev.device_id, "dev-1")


class ReadFailuresAndRecovery(unittest.TestCase):
    def test_timeout_degraded_recovering_connected_with_recovery_event(self):
        r = sim_rig([{"kind": "read_timeout", "start": 2, "duration": 2}])
        r.step()
        self.assertEqual(r.state, S.CONNECTED)
        seen = []
        for _ in range(4):
            r.step()
            seen.append(r.state)
        self.assertEqual(seen[0], S.DEGRADED)
        self.assertEqual(seen[1], S.DEGRADED)
        self.assertEqual(seen[-1], S.CONNECTED)
        self.assertIn(EC.RECOVERY, r.categories())
        rec = next(e for e in r.events.recent() if e.category == EC.RECOVERY)
        self.assertEqual(rec.details["failed_attempts"], 2)

    def test_stale_without_fabricated_values_during_outage(self):
        r = sim_rig([{"kind": "read_timeout", "start": 2, "duration": 50}])
        r.step()
        last = r.storage.measurements[-1].value
        for _ in range(3):
            r.step()
        r.clock.advance(5)
        v = r.tracker.snapshot().measurements[0]
        self.assertEqual(v.quality, Quality.STALE)
        self.assertEqual(v.value, last)
        self.assertEqual(len(r.storage.measurements), 1)  # nothing invented

    def test_link_drop_reconnects(self):
        r = sim_rig([{"kind": "link_drop", "start": 2}])
        r.step()
        r.step()
        self.assertEqual(r.state, S.DEGRADED)
        r.step()
        self.assertEqual(r.state, S.CONNECTED)
        self.assertEqual(r.adapter.connect_attempts, 2)

    def test_retry_limit_on_reads_ends_in_error(self):
        r = sim_rig([{"kind": "read_timeout", "start": 2, "duration": 100}])
        r.step()
        results = [r.step() for _ in range(4)]
        self.assertIsNone(results[-1])
        self.assertEqual(r.state, S.ERROR)

    def test_connected_socket_without_usable_data_is_not_healthy(self):
        faults = [
            {"kind": "read_timeout", "start": 2},
            {"kind": "missing", "key": "speed", "start": 3, "duration": 2},
        ]
        r = sim_rig(faults)
        r.step()
        r.step()
        self.assertEqual(r.state, S.DEGRADED)
        r.step()  # reconnect ok, but measurement missing
        self.assertEqual(r.state, S.DEGRADED)
        self.assertNotIn(EC.RECOVERY, r.categories())
        r.step()
        r.step()
        self.assertEqual(r.state, S.CONNECTED)
        self.assertIn(EC.RECOVERY, r.categories())

    def test_partial_data_degrades_without_failure_count(self):
        spec = device(
            measurements=[meas("a"), meas("b")],
            settings={"faults": [{"kind": "missing", "key": "a", "start": 1}]},
            retry=POLICY,
        )
        r = Rig(spec)
        r.step()
        snap = r.tracker.snapshot()
        self.assertEqual(r.state, S.DEGRADED)
        self.assertEqual(snap.consecutive_failures, 0)
        self.assertEqual(
            {m.key: m.quality for m in snap.measurements},
            {"a": Quality.MISSING, "b": Quality.VALID},
        )

    def test_invalid_value_marks_invalid_keeps_last_valid(self):
        r = sim_rig([{"kind": "invalid_value", "key": "speed", "mode": "nan", "start": 2}])
        r.step()
        good = r.storage.measurements[-1].value
        r.step()
        v = r.tracker.snapshot().measurements[0]
        self.assertEqual((v.quality, v.value), (Quality.INVALID, good))
        self.assertEqual(len(r.storage.measurements), 1)
        self.assertEqual(r.state, S.DEGRADED)

    def test_repeated_failures_do_not_flood_events(self):
        spec = device(
            settings={"faults": [{"kind": "read_timeout", "start": 2, "duration": 100}]},
            retry=RetryPolicy(1, 1, 1, 50),
        )
        rig = Rig(spec)
        rig.events = EventService(rig.storage, rig.clock, dedup_window_s=3600)
        rig.worker._events = rig.events
        rig.worker._pipeline._events = rig.events
        rig.step()
        for _ in range(30):
            rig.step()
        fails = [e for e in rig.events.recent(1000) if e.category == EC.ACQUISITION_FAILURE]
        self.assertLessEqual(len(fails), 2)


class HungAdapter(DeviceAdapter):
    adapter_type = "hung"
    source_kind = SourceKind.SIMULATOR

    def __init__(self, spec, clock):
        super().__init__(spec, clock)
        self.release = threading.Event()
        self.read_calls = 0

    @classmethod
    def validate_settings(cls, spec):
        return []

    def open(self): ...
    def connect(self): ...
    def read(self):
        self.read_calls += 1
        self.release.wait(10)
        return AdapterReadout(self.spec.device_id, ())

    def close(self):
        self.release.set() if False else None

    @property
    def connection_state(self):
        return S.CONNECTED


class Raising(HungAdapter):
    def read(self):
        raise ZeroDivisionError("boom")


class Fatal(HungAdapter):
    def read(self):
        raise AdapterFailure(
            ErrorInfo.create(C.INVALID_REGISTER_MAPPING, "bad map", "read", "dev-1")
        )


class GuardTests(unittest.TestCase):
    def test_hung_read_times_out_and_does_not_overlap(self):
        spec = device(retry=POLICY, response_timeout=0.05)
        adapter = HungAdapter(spec, ManualClock())
        r = Rig(spec, adapter, grace=0.05)
        try:
            r.step()
            self.assertEqual(r.state, S.DEGRADED)
            self.assertEqual(r.tracker.snapshot().last_error.category, C.READ_TIMEOUT)
            for _ in range(2):
                r.step()
            self.assertEqual(adapter.read_calls, 1)  # abandoned call still running: no new requests
            self.assertGreaterEqual(r.tracker.snapshot().cycles_skipped, 1)
        finally:
            adapter.release.set()

    def test_unexpected_exception_is_contained_as_device_error(self):
        spec = device(retry=POLICY)
        r = Rig(spec, Raising(spec, ManualClock()))
        self.assertIsNone(r.step())
        self.assertEqual(r.state, S.ERROR)
        self.assertEqual(r.tracker.snapshot().last_error.category, C.UNEXPECTED_ERROR)
        self.assertIn(EC.UNEXPECTED_ERROR, r.categories())

    def test_non_retryable_adapter_error_goes_straight_to_error(self):
        spec = device(retry=POLICY)
        a = Fatal(spec, ManualClock())
        r = Rig(spec, a)
        self.assertIsNone(r.step())
        self.assertEqual(r.state, S.ERROR)
        self.assertEqual(r.tracker.snapshot().consecutive_failures, 1)  # no retry loop
        self.assertIsNone(r.step())


class SnapshotSemantics(unittest.TestCase):
    def test_operating_state_separate_from_comm_and_unknown_when_stale(self):
        spec = device(
            retry=POLICY,
            settings={
                "operating_states": [{"from_read": 1, "state": "RUNNING"}],
                "faults": [{"kind": "read_timeout", "start": 2, "duration": 99}],
            },
        )
        r = Rig(spec)
        r.step()
        snap = r.tracker.snapshot()
        from textile_automation.domain.states import OperatingState

        self.assertEqual(
            (snap.comm_state, snap.operating.state), (S.CONNECTED, OperatingState.RUNNING)
        )
        self.assertTrue(snap.operating.is_current)
        for _ in range(3):
            r.step()
        r.clock.advance(10)
        snap = r.tracker.snapshot()
        self.assertIsNone(snap.operating.state)  # unknown, NOT "stopped"
        self.assertIn("does not mean it stopped", snap.operating.note)

    def test_no_operating_state_reported(self):
        r = sim_rig()
        r.step()
        self.assertIsNone(r.tracker.snapshot().operating.state)
        self.assertIn("Not reported", r.tracker.snapshot().operating.note)

    def test_restored_values_are_stale_until_reacquired(self):
        r = sim_rig()
        r.step()
        stored = r.storage.measurements[:]
        fresh = Rig(sim_rig().spec)
        fresh.tracker.restore_previous(stored)
        v = fresh.tracker.snapshot().measurements[0]
        self.assertEqual(
            (v.quality, v.from_previous_session, v.is_current), (Quality.STALE, True, False)
        )
        self.assertEqual(fresh.state, S.DISCONNECTED)  # not trusted as connected
        fresh.step()
        v = fresh.tracker.snapshot().measurements[0]
        self.assertEqual((v.quality, v.from_previous_session), (Quality.VALID, False))


if __name__ == "__main__":
    unittest.main()

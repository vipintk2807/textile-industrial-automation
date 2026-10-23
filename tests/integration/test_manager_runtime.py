"""Threaded tests with real worker threads and bounded waits (no arbitrary sleeps)."""

import threading
import unittest

from helpers import FakeStorage, device, meas, wait_until
from textile_automation.adapters.simulator import SimulatorAdapter
from textile_automation.clock import SystemClock
from textile_automation.domain.errors import OperationNotPermitted
from textile_automation.domain.models import (
    AppConfig,
    EventCategory as EC,
    RetryPolicy,
    StorageSettings,
)
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import CommunicationState as S
from textile_automation.runtime import AppRuntime, RuntimeStatus

FAST = RetryPolicy(
    initial_delay_s=0.05, max_delay_s=0.1, multiplier=2.0, max_consecutive_failures=3
)


def dev(did, faults=None, poll=0.05, **kw):
    return device(did, poll=poll, retry=FAST, settings={"faults": faults or []}, **kw)


def runtime(devices, storage=None, **cfg):
    storage = storage or FakeStorage()
    kw = {"storage": cfg.pop("storage_settings")} if "storage_settings" in cfg else {}
    config = AppConfig(
        devices=tuple(devices), shutdown_timeout_s=2.0, event_dedup_window_s=0.001, **kw
    )
    return AppRuntime(config, storage, guard_grace_s=0.1), storage


class ManagerThreadTests(unittest.TestCase):
    def test_failing_device_does_not_stop_healthy_device(self):
        rt, storage = runtime(
            [dev("good"), dev("bad", [{"kind": "connect_failure", "start": 1, "duration": 999}])]
        )
        rt.start()
        try:
            self.assertTrue(
                wait_until(lambda: sum(m.device_id == "good" for m in storage.measurements) >= 5)
            )
            snaps = {s.device_id: s for s in rt.snapshot().devices}
            self.assertEqual(snaps["good"].comm_state, S.CONNECTED)
            self.assertTrue(wait_until(lambda: rt.snapshot().devices[1].comm_state == S.ERROR))
            self.assertFalse(any(m.device_id == "bad" for m in storage.measurements))
        finally:
            rt.shutdown()

    def test_failing_device_bounded_connect_attempts(self):
        rt, _ = runtime([dev("bad", [{"kind": "connect_failure", "start": 1, "duration": 999}])])
        rt.start()
        try:
            self.assertTrue(wait_until(lambda: rt.snapshot().devices[0].comm_state == S.ERROR))
            attempts = rt.manager.workers["bad"].adapter.connect_attempts
            self.assertEqual(attempts, 3)
            self.assertFalse(
                wait_until(lambda: rt.manager.workers["bad"].adapter.connect_attempts > 3, 0.3)
            )
        finally:
            rt.shutdown()

    def test_start_is_idempotent_no_duplicate_threads(self):
        rt, _ = runtime([dev("a"), dev("b")])
        rt.start()
        rt.start()
        rt.manager.start()
        try:
            names = rt.manager.thread_names
            self.assertEqual(sorted(names), ["acq-a", "acq-b"])
            self.assertEqual(
                len([t for t in threading.enumerate() if t.name.startswith("acq-")]), 2
            )
        finally:
            rt.shutdown()

    def test_disabled_device_never_polled_and_has_no_thread(self):
        created = []

        def factory(spec, clock):
            created.append(spec.device_id)
            return SimulatorAdapter(spec, clock)

        config = AppConfig(
            devices=(dev("on"), dev("off", enabled=False)), event_dedup_window_s=0.001
        )
        rt = AppRuntime(config, FakeStorage(), adapter_factory=factory)
        rt.start()
        try:
            self.assertEqual(created, ["on"])
            snaps = {s.device_id: s for s in rt.snapshot().devices}
            self.assertEqual(snaps["off"].comm_state, S.DISABLED)
            self.assertEqual(rt.manager.thread_names, ["acq-on"])
        finally:
            rt.shutdown()

    def test_adapter_init_failure_isolated(self):
        def factory(spec, clock):
            if spec.device_id == "broken":
                raise RuntimeError("cannot init")
            return SimulatorAdapter(spec, clock)

        config = AppConfig(devices=(dev("broken"), dev("fine")), event_dedup_window_s=0.001)
        storage = FakeStorage()
        rt = AppRuntime(config, storage, adapter_factory=factory)
        rt.start()
        try:
            self.assertTrue(
                wait_until(lambda: any(m.device_id == "fine" for m in storage.measurements))
            )
            snaps = {s.device_id: s for s in rt.snapshot().devices}
            self.assertEqual(snaps["broken"].comm_state, S.ERROR)
            self.assertIn(EC.ADAPTER_INIT_FAILURE, [e.category for e in rt.events.recent()])
        finally:
            rt.shutdown()

    def test_shutdown_is_bounded_and_idempotent_even_with_long_timeouts(self):
        slow = dev(
            "slow", [{"kind": "read_timeout", "start": 1, "duration": 999}], response_timeout=30.0
        )
        rt, storage = runtime([slow])
        rt.start()
        self.assertTrue(wait_until(lambda: rt.manager.workers["slow"].adapter.read_attempts >= 1))
        t0 = SystemClock().monotonic()
        report = rt.shutdown()
        elapsed = SystemClock().monotonic() - t0
        self.assertLess(elapsed, 5.0)
        self.assertTrue(report.stopped_cleanly, report)
        self.assertIsNone(rt.shutdown())  # second call is a no-op
        self.assertTrue(storage.closed)
        self.assertEqual(rt.status, RuntimeStatus.STOPPED)

    def test_no_new_cycles_after_shutdown(self):
        rt, storage = runtime([dev("a")])
        rt.start()
        self.assertTrue(wait_until(lambda: len(storage.measurements) >= 2))
        rt.shutdown()
        count = len(storage.measurements)
        self.assertFalse(wait_until(lambda: len(storage.measurements) > count, 0.3))
        self.assertEqual(rt.manager.thread_names, [])


class RuntimeEndToEnd(unittest.TestCase):
    def test_full_workflow_with_fault_recovery_and_restart(self):
        storage = FakeStorage()
        rt, _ = runtime(
            [
                dev(
                    "sim-1",
                    [{"kind": "read_timeout", "start": 4, "duration": 2}],
                    measurements=[meas("speed", mapping={"base": 50.0})],
                )
            ],
            storage,
        )
        rt.start()
        try:
            self.assertTrue(
                wait_until(lambda: any(e.category == EC.RECOVERY for e in rt.events.recent()))
            )
            self.assertTrue(wait_until(lambda: rt.snapshot().devices[0].comm_state == S.CONNECTED))
            snap = rt.snapshot()
            self.assertTrue(snap.any_simulated)
            cats = {e.category for e in storage.events}
            self.assertTrue(
                {EC.STARTUP, EC.COMMUNICATION_STATE_CHANGE, EC.ACQUISITION_FAILURE, EC.RECOVERY}
                <= cats
            )
            self.assertTrue(all(m.source_kind.value == "SIMULATOR" for m in storage.measurements))
        finally:
            report = rt.shutdown()
        self.assertTrue(report.stopped_cleanly)
        self.assertIn(EC.SHUTDOWN, {e.category for e in storage.events})

        # restart on the same storage: history restored but never presented as fresh
        rt2, _ = runtime(
            [dev("sim-1", [], measurements=[meas("speed", mapping={"base": 50.0})], poll=60.0)],
            storage,
        )
        rt2.manager.register_devices()
        rt2.manager.trackers["sim-1"].restore_previous(
            [storage.latest_measurement("sim-1", "speed")]
        )
        first = rt2.manager.trackers["sim-1"].snapshot()
        self.assertEqual(first.comm_state, S.DISCONNECTED)
        self.assertEqual(first.measurements[0].quality, Quality.STALE)
        self.assertTrue(first.measurements[0].from_previous_session)

    def test_restart_restores_via_start(self):
        storage = FakeStorage()
        rt, _ = runtime([dev("a", poll=60.0)], storage)
        rt.start()
        self.assertTrue(wait_until(lambda: len(storage.measurements) >= 1))
        rt.shutdown()
        storage.closed = False
        rt2, _ = runtime(
            [dev("a", [{"kind": "connect_failure", "start": 1, "duration": 99}], poll=60.0)],
            storage,
        )
        rt2.start()
        try:
            v = rt2.snapshot().devices[0].measurements[0]
            self.assertTrue(v.from_previous_session)
            self.assertEqual(v.quality, Quality.STALE)
            self.assertFalse(v.is_current)
        finally:
            rt2.shutdown()

    def test_persistence_failure_keeps_monitoring_visible(self):
        rt, storage = runtime([dev("a")])
        storage.fail = True
        rt.start()  # upsert fails -> FAILED start, nothing acquired
        self.assertEqual(rt.status, RuntimeStatus.FAILED)
        self.assertTrue(
            any("Database is not usable" in str(i) for i in rt.snapshot().startup_issues)
        )
        self.assertEqual(rt.manager.thread_names, [])

    def test_mid_run_storage_failure_enters_restricted_mode_and_recovers(self):
        rt, storage = runtime([dev("a")])
        rt.start()
        try:
            self.assertTrue(wait_until(lambda: len(storage.measurements) >= 2))
            storage.fail = True
            self.assertTrue(
                wait_until(lambda: rt.snapshot().persistence.mode.value == "RESTRICTED")
            )
            self.assertEqual(
                rt.snapshot().devices[0].comm_state, S.CONNECTED
            )  # live view still works
            storage.fail = False
            self.assertTrue(wait_until(lambda: rt.snapshot().persistence.mode.value == "NORMAL"))
            self.assertGreater(rt.snapshot().persistence.dropped_measurements, 0)
        finally:
            rt.shutdown()

    def test_failed_runtime_surfaces_config_issues(self):
        from textile_automation.domain.errors import SettingIssue

        rt = AppRuntime.failed(
            [SettingIssue("devices[0].poll_interval_s", "must be > 0")], config_path="x.toml"
        )
        rt.start()
        rt.shutdown()
        snap = rt.snapshot()
        self.assertEqual(snap.devices, ())
        self.assertEqual(str(snap.startup_issues[0]), "devices[0].poll_interval_s: must be > 0")

    def test_simulator_fault_injection_and_refusal(self):
        rt, storage = runtime([dev("a", poll=0.05)])
        rt.start()
        try:
            self.assertTrue(wait_until(lambda: len(storage.measurements) >= 1))
            rt.inject_simulator_fault("a", kind="read_timeout", duration=2)
            self.assertTrue(
                wait_until(
                    lambda: any(e.category == EC.ACQUISITION_FAILURE for e in rt.events.recent())
                )
            )
            self.assertTrue(any(e.category == EC.SIMULATOR_CONTROL for e in rt.events.recent()))
            with self.assertRaises(OperationNotPermitted):
                rt.inject_simulator_fault("missing-device", kind="read_timeout")
        finally:
            rt.shutdown()

    def test_retention_purges_old_measurements(self):
        storage = FakeStorage()
        rt, _ = runtime(
            [dev("a", poll=60.0)], storage, storage_settings=StorageSettings(retention_days=1)
        )
        rt.start()
        try:
            self.assertTrue(
                wait_until(lambda: any(e.category == EC.RETENTION for e in rt.events.recent()))
            )
        finally:
            rt.shutdown()


if __name__ == "__main__":
    unittest.main()

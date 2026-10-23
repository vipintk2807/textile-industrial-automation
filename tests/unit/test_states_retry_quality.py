import unittest

from textile_automation.clock import ManualClock
from textile_automation.domain.errors import ErrorCategory, ErrorInfo
from textile_automation.domain.models import MeasurementResult, RetryPolicy
from textile_automation.domain.quality import (
    MeasurementState,
    Quality,
    assess_measurement,
    freshness_threshold_s,
)
from textile_automation.domain.states import (
    CommEvent,
    CommunicationState as S,
    InvalidTransition,
    SourceKind,
    next_comm_state,
)
from textile_automation.services.retry import backoff_delay, retry_limit_reached


class TransitionTests(unittest.TestCase):
    def test_happy_path(self):
        s = next_comm_state(S.DISCONNECTED, CommEvent.BEGIN_CONNECT)
        self.assertEqual(s, S.CONNECTING)
        s = next_comm_state(s, CommEvent.CONNECT_OK)
        self.assertEqual(s, S.CONNECTING)  # connected is not healthy until data verified
        self.assertEqual(next_comm_state(s, CommEvent.READ_FULL), S.CONNECTED)

    def test_partial_and_none_are_degraded(self):
        self.assertEqual(next_comm_state(S.CONNECTED, CommEvent.READ_PARTIAL), S.DEGRADED)
        self.assertEqual(next_comm_state(S.RECOVERING, CommEvent.READ_NONE), S.DEGRADED)

    def test_failure_then_recovery_path(self):
        s = next_comm_state(S.CONNECTED, CommEvent.READ_FAIL)
        self.assertEqual(s, S.DEGRADED)
        s = next_comm_state(s, CommEvent.BEGIN_RECOVERY)
        self.assertEqual(s, S.RECOVERING)
        self.assertEqual(next_comm_state(s, CommEvent.CONNECT_FAIL), S.RECOVERING)

    def test_never_connected_connect_failure_is_disconnected(self):
        self.assertEqual(next_comm_state(S.CONNECTING, CommEvent.CONNECT_FAIL), S.DISCONNECTED)

    def test_error_states(self):
        for live in (S.CONNECTING, S.RECOVERING, S.CONNECTED, S.DEGRADED, S.DISCONNECTED):
            self.assertEqual(next_comm_state(live, CommEvent.GIVE_UP), S.ERROR)
            self.assertEqual(next_comm_state(live, CommEvent.FATAL), S.ERROR)
        self.assertEqual(next_comm_state(S.ERROR, CommEvent.OPERATOR_RETRY), S.DISCONNECTED)

    def test_disabled_has_no_transitions(self):
        for ev in CommEvent:
            with self.assertRaises(InvalidTransition):
                next_comm_state(S.DISABLED, ev)

    def test_undefined_transition_raises(self):
        with self.assertRaises(InvalidTransition):
            next_comm_state(S.CONNECTED, CommEvent.CONNECT_FAIL)

    def test_stop_from_every_active_state(self):
        for st in S:
            if st is S.DISABLED:
                continue
            self.assertEqual(next_comm_state(st, CommEvent.STOP), S.DISCONNECTED)


class RetryTests(unittest.TestCase):
    policy = RetryPolicy(
        initial_delay_s=1.0, max_delay_s=10.0, multiplier=2.0, max_consecutive_failures=6
    )

    def test_sequence_is_capped(self):
        self.assertEqual([backoff_delay(self.policy, n) for n in range(1, 7)], [1, 2, 4, 8, 10, 10])

    def test_huge_failure_count_does_not_overflow(self):
        self.assertEqual(backoff_delay(self.policy, 10_000), 10.0)

    def test_zero_failures_rejected(self):
        with self.assertRaises(ValueError):
            backoff_delay(self.policy, 0)

    def test_limit(self):
        self.assertFalse(retry_limit_reached(self.policy, 5))
        self.assertTrue(retry_limit_reached(self.policy, 6))


def _res(clock, value, quality=Quality.VALID, error=None):
    return MeasurementResult(
        "d", "speed", value, "rpm", clock.now(), SourceKind.SIMULATOR, quality, error=error
    )


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()

    def view(self, state, threshold=3.0):
        return assess_measurement(
            state, name="Speed", unit="rpm", now=self.clock.now(), threshold_s=threshold
        )

    def test_threshold_derives_from_interval(self):
        self.assertEqual(freshness_threshold_s(2.0, 3.0), 6.0)

    def test_never_received_is_unknown(self):
        v = self.view(MeasurementState("speed"))
        self.assertEqual(v.quality, Quality.UNKNOWN)
        self.assertIsNone(v.value)
        self.assertFalse(v.is_current)

    def test_fresh_valid(self):
        r = _res(self.clock, 5.0)
        v = self.view(MeasurementState("speed", r, False, r))
        self.assertEqual((v.quality, v.is_current, v.value), (Quality.VALID, True, 5.0))

    def test_zero_is_a_valid_value_not_missing(self):
        r = _res(self.clock, 0.0)
        v = self.view(MeasurementState("speed", r, False, r))
        self.assertEqual(v.quality, Quality.VALID)
        self.assertEqual(v.value, 0.0)

    def test_becomes_stale_without_replacement_value(self):
        r = _res(self.clock, 7.5)
        st = MeasurementState("speed", r, False, r)
        self.clock.advance(3.0)
        self.assertEqual(self.view(st).quality, Quality.VALID)  # exactly at the limit
        self.clock.advance(0.5)
        v = self.view(st)
        self.assertEqual((v.quality, v.value, v.is_current), (Quality.STALE, 7.5, False))

    def test_previous_session_value_is_always_stale(self):
        r = _res(self.clock, 1.0)
        v = self.view(MeasurementState("speed", r, True, None))
        self.assertEqual(v.quality, Quality.STALE)
        self.assertTrue(v.from_previous_session)

    def test_latest_invalid_wins_while_fresh_but_keeps_last_valid(self):
        good = _res(self.clock, 5.0)
        self.clock.advance(1)
        err = ErrorInfo.create(ErrorCategory.NON_FINITE_VALUE, "nan", "validate", "d")
        bad = _res(self.clock, None, Quality.INVALID, err)
        v = self.view(MeasurementState("speed", good, False, bad))
        self.assertEqual((v.quality, v.value, v.is_current), (Quality.INVALID, 5.0, False))
        self.assertIn("nan", v.note)

    def test_old_invalid_degrades_to_stale_when_value_exists(self):
        good = _res(self.clock, 5.0)
        err = ErrorInfo.create(ErrorCategory.NON_FINITE_VALUE, "nan", "validate", "d")
        bad = _res(self.clock, None, Quality.INVALID, err)
        self.clock.advance(10)
        self.assertEqual(
            self.view(MeasurementState("speed", good, False, bad)).quality, Quality.STALE
        )

    def test_only_missing_attempts_stays_missing(self):
        miss = _res(self.clock, None, Quality.MISSING)
        self.clock.advance(100)
        self.assertEqual(
            self.view(MeasurementState("speed", None, False, miss)).quality, Quality.MISSING
        )


if __name__ == "__main__":
    unittest.main()


class TimedCallBusyRegression(unittest.TestCase):
    """A thread that lingers after its function returned must not make the call look busy."""

    def test_finished_call_is_not_busy_even_if_thread_lingers(self):
        import threading
        from unittest import mock

        from textile_automation.services import timed_call

        class LingeringThread(threading.Thread):
            def is_alive(self):  # simulates the short window after the function has returned
                return True

        tc = timed_call.TimedCall("x")
        with mock.patch.object(timed_call.threading, "Thread", LingeringThread):
            for _ in range(50):
                self.assertEqual(tc.run(lambda: 7, 1.0), 7)
                self.assertFalse(tc.busy)

    def test_hung_call_is_busy_until_it_finishes(self):
        import threading

        from textile_automation.services.timed_call import CallBusy, CallTimeout, TimedCall

        release = threading.Event()
        tc = TimedCall("x")
        with self.assertRaises(CallTimeout):
            tc.run(lambda: release.wait(10), 0.05)
        self.assertTrue(tc.busy)
        with self.assertRaises(CallBusy):
            tc.run(lambda: 1, 0.05)
        release.set()
        from helpers import wait_until

        self.assertTrue(wait_until(lambda: not tc.busy))
        self.assertEqual(tc.run(lambda: 2, 1.0), 2)

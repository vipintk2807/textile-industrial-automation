import math
import unittest

from helpers import device, meas
from textile_automation.adapters.registry import ADAPTERS, create_adapter, unsupported_message
from textile_automation.adapters.simulator import SimulatorAdapter, generate_value
from textile_automation.clock import ManualClock
from textile_automation.domain.errors import (
    AdapterFailure,
    ErrorCategory as C,
    OperationNotPermitted,
)
from textile_automation.domain.models import DataType
from textile_automation.domain.states import CommunicationState, SourceKind


def sim(settings=None, measurements=None, **kw):
    spec = device(settings=settings or {}, measurements=measurements, **kw)
    adapter = SimulatorAdapter(spec, ManualClock(), sleep=lambda s: False)
    adapter.open()
    return adapter


MEAS = [
    meas("speed", mapping={"base": 100.0, "amplitude": 10.0, "period_cycles": 20, "noise": 1.0})
]


class DeterminismTests(unittest.TestCase):
    def values(self, seed, n=8):
        a = sim({"seed": seed}, MEAS)
        a.connect()
        return [a.read().results[0].value for _ in range(n)]

    def test_same_seed_same_sequence(self):
        self.assertEqual(self.values(42), self.values(42))

    def test_different_seed_differs(self):
        self.assertNotEqual(self.values(1), self.values(2))

    def test_generate_value_independent_of_call_order(self):
        spec = MEAS[0]
        a = [generate_value(spec, 7, n) for n in (5, 1, 3)]
        b = [generate_value(spec, 7, n) for n in (1, 3, 5)]
        self.assertEqual(sorted(a), sorted(b))

    def test_values_within_documented_synthetic_envelope(self):
        for v in self.values(3, 40):
            self.assertTrue(100 - 11.000001 <= v <= 100 + 11.000001)

    def test_every_reading_is_labelled_simulated(self):
        a = sim({}, MEAS)
        a.connect()
        for r in a.read().results:
            self.assertEqual(r.source_kind, SourceKind.SIMULATOR)
        self.assertEqual(SimulatorAdapter.source_kind, SourceKind.SIMULATOR)

    def test_units_and_keys_follow_config(self):
        a = sim({}, MEAS)
        a.connect()
        r = a.read().results[0]
        self.assertEqual((r.measurement_key, r.unit, r.device_id), ("speed", "rpm", "dev-1"))

    def test_bool_str_int_generators(self):
        specs = [
            meas("on", data_type=DataType.BOOL, unit=None, mapping={"period_cycles": 2}),
            meas(
                "mode",
                data_type=DataType.STR,
                unit=None,
                mapping={"choices": ["a", "b"], "period_cycles": 1},
            ),
            meas("count", data_type=DataType.INT, unit="pcs", mapping={"base": 5.0}),
        ]
        a = sim({}, specs)
        a.connect()
        rows = [{r.measurement_key: r.value for r in a.read().results} for _ in range(4)]
        self.assertEqual([r["on"] for r in rows], [False, False, True, True])
        self.assertEqual([r["mode"] for r in rows], ["a", "b", "a", "b"])
        self.assertEqual(rows[0]["count"], 5)
        self.assertIsInstance(rows[0]["count"], int)

    def test_source_timestamp_optional(self):
        a = sim({"provide_source_timestamp": False}, MEAS)
        a.connect()
        self.assertIsNone(a.read().results[0].source_timestamp)


class FaultTests(unittest.TestCase):
    def test_read_requires_connection(self):
        a = sim({}, MEAS)
        with self.assertRaises(AdapterFailure) as cm:
            a.read()
        self.assertEqual(cm.exception.error.category, C.CONNECTION_FAILURE)

    def test_connect_failure_window(self):
        a = sim({"faults": [{"kind": "connect_failure", "start": 1, "duration": 2}]}, MEAS)
        for _ in range(2):
            with self.assertRaises(AdapterFailure) as cm:
                a.connect()
            self.assertTrue(cm.exception.error.retryable)
        a.connect()
        self.assertEqual(a.connection_state, CommunicationState.CONNECTED)

    def test_connect_timeout(self):
        a = sim({"faults": [{"kind": "connect_timeout", "start": 1}]}, MEAS)
        with self.assertRaises(AdapterFailure) as cm:
            a.connect()
        self.assertEqual(cm.exception.error.category, C.CONNECTION_TIMEOUT)

    def test_read_timeout_then_recovery(self):
        a = sim({"faults": [{"kind": "read_timeout", "start": 2, "duration": 2}]}, MEAS)
        a.connect()
        a.read()
        for _ in range(2):
            with self.assertRaises(AdapterFailure) as cm:
                a.read()
            self.assertEqual(cm.exception.error.category, C.READ_TIMEOUT)
        self.assertEqual(len(a.read().results), 1)

    def test_link_drop_requires_reconnect(self):
        a = sim({"faults": [{"kind": "link_drop", "start": 1}]}, MEAS)
        a.connect()
        with self.assertRaises(AdapterFailure):
            a.read()
        self.assertEqual(a.connection_state, CommunicationState.DISCONNECTED)
        a.connect()
        self.assertEqual(len(a.read().results), 1)

    def test_protocol_error(self):
        a = sim({"faults": [{"kind": "read_protocol_error", "start": 1}]}, MEAS)
        a.connect()
        with self.assertRaises(AdapterFailure) as cm:
            a.read()
        self.assertEqual(cm.exception.error.category, C.PROTOCOL_EXCEPTION)

    def test_missing_measurement_is_absent_not_zero(self):
        specs = [meas("a"), meas("b")]
        a = sim({"faults": [{"kind": "missing", "key": "a", "start": 1}]}, specs)
        a.connect()
        self.assertEqual([r.measurement_key for r in a.read().results], ["b"])

    def test_invalid_value_modes(self):
        for mode, check in (("nan", math.isnan), ("inf", math.isinf)):
            a = sim(
                {"faults": [{"kind": "invalid_value", "key": "speed", "mode": mode, "start": 1}]},
                MEAS,
            )
            a.connect()
            self.assertTrue(check(a.read().results[0].value))
        a = sim(
            {"faults": [{"kind": "invalid_value", "key": "speed", "mode": "text", "start": 1}]},
            MEAS,
        )
        a.connect()
        self.assertIsInstance(a.read().results[0].value, str)

    def test_slow_response_below_timeout_succeeds(self):
        slept = []
        spec = device(
            settings={"faults": [{"kind": "slow_response", "delay_s": 0.01, "start": 1}]},
            measurements=MEAS,
            response_timeout=1.0,
        )
        a = SimulatorAdapter(spec, ManualClock(), sleep=lambda s: slept.append(s) or False)
        a.open()
        a.connect()
        self.assertEqual(len(a.read().results), 1)
        self.assertEqual(slept, [0.01])

    def test_slow_response_beyond_timeout_times_out(self):
        spec = device(
            settings={"faults": [{"kind": "slow_response", "delay_s": 5, "start": 1}]},
            measurements=MEAS,
            response_timeout=0.5,
        )
        a = SimulatorAdapter(spec, ManualClock(), sleep=lambda s: False)
        a.open()
        a.connect()
        with self.assertRaises(AdapterFailure) as cm:
            a.read()
        self.assertEqual(cm.exception.error.category, C.READ_TIMEOUT)

    def test_close_interrupts_simulated_delay(self):
        import threading

        spec = device(
            settings={"faults": [{"kind": "read_timeout", "start": 1}]},
            measurements=MEAS,
            response_timeout=30.0,
        )
        a = SimulatorAdapter(spec, ManualClock())
        a.open()
        a.connect()
        errors = []

        def run():
            try:
                a.read()
            except AdapterFailure as exc:
                errors.append(exc.error)

        t = threading.Thread(target=run)
        t.start()
        a.close()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors[0].category, C.RESOURCE_ERROR)

    def test_operating_state_is_synthetic_and_scheduled(self):
        a = sim(
            {
                "operating_states": [
                    {"from_read": 1, "state": "RUNNING"},
                    {"from_read": 3, "state": "IDLE"},
                ]
            },
            MEAS,
        )
        a.connect()
        states = [a.read().operating_report.state.value for _ in range(4)]
        self.assertEqual(states, ["RUNNING", "RUNNING", "IDLE", "IDLE"])
        self.assertTrue(a.read().operating_report.raw_value.startswith("synthetic"))

    def test_no_operating_state_when_not_configured(self):
        a = sim({}, MEAS)
        a.connect()
        self.assertIsNone(a.read().operating_report)

    def test_dynamic_injection_targets_next_attempt(self):
        a = sim({}, MEAS)
        a.connect()
        a.read()
        a.inject_fault(kind="read_timeout", duration=1)
        with self.assertRaises(AdapterFailure):
            a.read()
        a.read()

    def test_injection_validates(self):
        a = sim({}, MEAS)
        with self.assertRaises(ValueError):
            a.inject_fault(kind="explode")
        with self.assertRaises(ValueError):
            a.inject_fault(kind="missing", key="nope")

    def test_clear_injected_faults_keeps_configured(self):
        a = sim({"faults": [{"kind": "read_timeout", "start": 99}]}, MEAS)
        a.inject_fault(kind="read_timeout")
        a.clear_injected_faults()
        self.assertEqual(len(a._faults), 1)


class ValidationAndRegistryTests(unittest.TestCase):
    def issues(self, settings=None, measurements=None):
        return [
            str(i)
            for i in SimulatorAdapter.validate_settings(
                device(settings=settings or {}, measurements=measurements or MEAS)
            )
        ]

    def test_valid_config_has_no_issues(self):
        self.assertEqual(self.issues(), [])

    def test_unknown_and_bad_fault_settings(self):
        out = "\n".join(self.issues({"bogus": 1, "faults": [{"kind": "x", "start": 0}]}))
        self.assertIn("unknown simulator setting", out)
        self.assertIn("kind", out)

    def test_fault_key_must_exist(self):
        self.assertTrue(
            any(
                "key" in i
                for i in self.issues({"faults": [{"kind": "missing", "key": "zz", "start": 1}]})
            )
        )

    def test_numeric_requires_base(self):
        out = self.issues(measurements=[meas("x", mapping={})])
        self.assertTrue(any("base" in i for i in out))

    def test_synthetic_range_vs_limits_flagged(self):
        spec = meas("x", mapping={"base": 100, "amplitude": 50}, max_value=120)
        self.assertTrue(any("exceeds max_value" in i for i in self.issues(measurements=[spec])))

    def test_unknown_parameter_flagged(self):
        spec = meas("x", mapping={"base": 1, "wobble": 3})
        self.assertTrue(
            any("unknown simulator parameter" in i for i in self.issues(measurements=[spec]))
        )

    def test_registry_only_has_simulator(self):
        self.assertEqual(set(ADAPTERS), {"simulator"})

    def test_modbus_is_reported_as_deferred(self):
        self.assertIn("deferred", unsupported_message("modbus_tcp"))
        with self.assertRaises(ValueError):
            create_adapter(device(adapter_type="modbus_tcp"), ManualClock())

    def test_fault_injection_refused_for_base_adapter_contract(self):
        from textile_automation.adapters.base import DeviceAdapter

        class Dummy(DeviceAdapter):
            adapter_type = "dummy"
            source_kind = SourceKind.PHYSICAL

            @classmethod
            def validate_settings(cls, spec):
                return []

            def open(self): ...
            def connect(self): ...
            def read(self): ...
            def close(self): ...
            @property
            def connection_state(self):
                return CommunicationState.DISCONNECTED

        with self.assertRaises(OperationNotPermitted):
            Dummy(device(), ManualClock()).inject_fault(kind="x")


if __name__ == "__main__":
    unittest.main()

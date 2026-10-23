import copy
import tomllib
import unittest
from pathlib import Path

from textile_automation.config.build import build_app_config
from textile_automation.config.errors import ConfigError
from textile_automation.domain.models import DataType

ROOT = Path(__file__).resolve().parents[2]


def minimal():
    return {
        "schema_version": 1,
        "devices": [
            {
                "device_id": "d1",
                "name": "Device",
                "adapter_type": "simulator",
                "measurements": [{"key": "speed", "unit": "rpm", "source_mapping": {"base": 10.0}}],
            }
        ],
    }


def issues(raw):
    with unittest.TestCase().assertRaises(ConfigError) as cm:
        build_app_config(raw)
    return [str(i) for i in cm.exception.issues]


class ValidConfig(unittest.TestCase):
    def test_minimal_accepted_with_documented_defaults(self):
        cfg = build_app_config(minimal())
        d = cfg.devices[0]
        self.assertEqual(
            (d.poll_interval_s, d.connect_timeout_s, d.stale_after_intervals, d.enabled),
            (1.0, 2.0, 3.0, True),
        )
        self.assertEqual(d.measurements[0].data_type, DataType.FLOAT)
        self.assertEqual(d.measurements[0].name, "speed")
        self.assertIsNone(cfg.storage.retention_days)  # keep-all is the documented default

    def test_shipped_examples_are_valid(self):
        for rel in ("config/devices.example.toml", "examples/simulator_config.toml"):
            raw = tomllib.loads((ROOT / rel).read_text(encoding="utf-8"))
            cfg = build_app_config(raw)
            self.assertTrue(cfg.devices, rel)
        raw = tomllib.loads((ROOT / "config/devices.example.toml").read_text(encoding="utf-8"))
        cfg = build_app_config(raw)
        self.assertEqual([d.enabled for d in cfg.devices], [True, True, False])

    def test_examples_contain_no_secrets_or_network_addresses(self):
        for rel in ("config/devices.example.toml", "examples/simulator_config.toml"):
            text = (ROOT / rel).read_text(encoding="utf-8").lower()
            for word in ("password", "token", "secret", "192.168.", "10.0."):
                self.assertNotIn(word, text.replace("do not put passwords, tokens", ""), rel)


class InvalidConfig(unittest.TestCase):
    def mutate(self, fn):
        raw = copy.deepcopy(minimal())
        fn(raw)
        return "\n".join(issues(raw))

    def test_missing_required_fields(self):
        out = self.mutate(lambda r: r["devices"][0].pop("name"))
        self.assertIn("devices[0].name: is required", out)
        out = self.mutate(lambda r: r.pop("schema_version"))
        self.assertIn("schema_version", out)

    def test_no_devices(self):
        self.assertIn("at least one", "\n".join(issues({"schema_version": 1, "devices": []})))

    def test_duplicate_device_id(self):
        def f(r):
            r["devices"].append(copy.deepcopy(r["devices"][0]))

        self.assertIn("duplicate device_id 'd1'", self.mutate(f))

    def test_duplicate_measurement_key(self):
        def f(r):
            r["devices"][0]["measurements"].append(
                copy.deepcopy(r["devices"][0]["measurements"][0])
            )

        self.assertIn("duplicate measurement key", self.mutate(f))

    def test_unsupported_adapter_types(self):
        out = self.mutate(lambda r: r["devices"][0].update(adapter_type="plc9000"))
        self.assertIn("unsupported adapter 'plc9000'", out)
        out = self.mutate(lambda r: r["devices"][0].update(adapter_type="modbus_tcp"))
        self.assertIn("deferred", out)

    def test_invalid_polling_intervals(self):
        for bad in (0, -1, 0.0, 1e9, float("nan"), True, "1"):
            out = self.mutate(lambda r, b=bad: r["devices"][0].update(poll_interval_s=b))
            self.assertIn("poll_interval_s", out, bad)

    def test_invalid_timeouts(self):
        for key in ("connect_timeout_s", "response_timeout_s"):
            for bad in (0, -5, 10_000, "x"):
                out = self.mutate(lambda r, k=key, b=bad: r["devices"][0].update({k: b}))
                self.assertIn(key, out)

    def test_invalid_measurement_definitions(self):
        cases = {
            "key": ("BadKey", "key"),
            "data_type": ("complex", "data_type"),
            "min_value": ("low", "min_value"),
        }
        for field, (bad, expect) in cases.items():
            out = self.mutate(
                lambda r, f=field, b=bad: r["devices"][0]["measurements"][0].update({f: b})
            )
            self.assertIn(expect, out)

    def test_min_greater_than_max(self):
        def f(r):
            r["devices"][0]["measurements"][0].update(min_value=10, max_value=1)

        self.assertIn("max_value: must be >= min_value", self.mutate(f))

    def test_unit_and_limits_only_for_numeric(self):
        def f(r):
            r["devices"][0]["measurements"][0].update(
                data_type="str", unit="rpm", min_value=1, source_mapping={"choices": ["a"]}
            )

        out = self.mutate(f)
        self.assertIn("units apply only", out)
        self.assertIn("limits apply only", out)

    def test_unknown_keys_are_rejected_not_reinterpreted(self):
        out = self.mutate(lambda r: r["devices"][0].update(pollinterval_s=1))
        self.assertIn("devices[0].pollinterval_s: unknown setting", out)
        out = self.mutate(lambda r: r.update(extra_section={}))
        self.assertIn("extra_section", out)

    def test_retry_policy_bounds(self):
        out = self.mutate(
            lambda r: r["devices"][0].update(retry={"initial_delay_s": 10, "max_delay_s": 1})
        )
        self.assertIn("max_delay_s: must be >= initial_delay_s", out)
        out = self.mutate(lambda r: r["devices"][0].update(retry={"max_consecutive_failures": 0}))
        self.assertIn("max_consecutive_failures", out)

    def test_plaintext_secret_rejected(self):
        out = self.mutate(
            lambda r: r["devices"][0].update(adapter_settings={"seed": 1, "password": "hunter2"})
        )
        self.assertIn("secrets must not be stored", out)
        self.assertNotIn("hunter2", out)  # the value is never echoed

    def test_simulator_specific_validation_runs(self):
        out = self.mutate(
            lambda r: r["devices"][0].update(
                adapter_settings={"faults": [{"kind": "nope", "start": 1}]}
            )
        )
        self.assertIn("devices[0].adapter_settings.faults[0].kind", out)

    def test_unsupported_schema_version(self):
        raw = minimal()
        raw["schema_version"] = 2
        self.assertIn("unsupported version", "\n".join(issues(raw)))

    def test_all_problems_reported_together(self):
        raw = minimal()
        raw["devices"][0].update(poll_interval_s=-1, connect_timeout_s=0, adapter_type="x")
        self.assertGreaterEqual(len(issues(raw)), 3)

    def test_bad_application_settings(self):
        raw = minimal()
        raw["application"] = {"log_level": "LOUD", "ui_refresh_seconds": 0}
        out = "\n".join(issues(raw))
        self.assertIn("application.log_level", out)
        self.assertIn("application.ui_refresh_seconds", out)

    def test_retention_bounds(self):
        raw = minimal()
        raw["storage"] = {"retention_days": 0}
        self.assertIn("storage.retention_days", "\n".join(issues(raw)))


class LoaderFileErrors(unittest.TestCase):
    def test_missing_and_malformed_files(self):
        import tempfile
        from textile_automation.config.loader import load_config

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ConfigError) as cm:
                load_config(Path(d) / "nope.toml")
            self.assertIn("Cannot read", str(cm.exception))
            bad = Path(d) / "bad.toml"
            bad.write_text("schema_version = [1,\n", encoding="utf-8")
            with self.assertRaises(ConfigError) as cm:
                load_config(bad)
            self.assertIn("Not valid TOML", str(cm.exception))
            self.assertIn("bad.toml", str(cm.exception))


class BomTolerance(unittest.TestCase):
    def test_toml_with_utf8_bom_is_read(self):
        import tempfile
        from textile_automation.config.loader import read_toml_file

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_bytes(b"\xef\xbb\xbf" + b"schema_version = 1\n")
            self.assertEqual(read_toml_file(p), {"schema_version": 1})

    def test_non_utf8_file_gives_clear_error(self):
        import tempfile
        from textile_automation.config.loader import read_toml_file

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_bytes(b'name = "\xff\xfe"\n')
            with self.assertRaises(ConfigError) as cm:
                read_toml_file(p)
            self.assertIn("not UTF-8", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

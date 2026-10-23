"""Pydantic shape validation. Requires pydantic (skipped if not installed)."""

import tempfile
import unittest
from pathlib import Path

from helpers import requires_pydantic

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


@requires_pydantic
class ShapeTests(unittest.TestCase):
    def errors(self, raw):
        from textile_automation.config.errors import ConfigError
        from textile_automation.config.loader import parse_config

        with self.assertRaises(ConfigError) as cm:
            parse_config(raw)
        return "\n".join(str(i) for i in cm.exception.issues)

    def test_valid_minimal_and_examples(self):
        from textile_automation.config.loader import load_config, parse_config

        self.assertEqual(parse_config(minimal()).devices[0].device_id, "d1")
        for rel in ("config/devices.example.toml", "examples/simulator_config.toml"):
            self.assertTrue(load_config(ROOT / rel).devices)

    def test_missing_required_field(self):
        raw = minimal()
        del raw["devices"][0]["name"]
        self.assertIn("devices[0].name: is required", self.errors(raw))

    def test_wrong_types(self):
        raw = minimal()
        raw["devices"][0]["enabled"] = "yes"
        raw["devices"][0]["poll_interval_s"] = "fast"
        out = self.errors(raw)
        self.assertIn("devices[0].enabled", out)
        self.assertIn("devices[0].poll_interval_s: must be a number", out)  # both reported together

    def test_bool_is_not_a_number(self):
        raw = minimal()
        raw["devices"][0]["poll_interval_s"] = True
        self.assertIn("devices[0].poll_interval_s: must be a number", self.errors(raw))

    def test_unknown_key_reported_with_path(self):
        raw = minimal()
        raw["devices"][0]["measurements"][0]["unitt"] = "x"
        self.assertIn("devices[0].measurements[0].unitt: unknown setting", self.errors(raw))

    def test_malformed_file_end_to_end(self):
        from textile_automation.config.errors import ConfigError
        from textile_automation.config.loader import load_config

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text("schema_version = 1\n[[devices]]\ndevice_id = 5\n", encoding="utf-8")
            with self.assertRaises(ConfigError) as cm:
                load_config(p)
            self.assertIn("device_id", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

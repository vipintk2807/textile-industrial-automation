"""Streamlit UI smoke tests using streamlit.testing.v1.AppTest (requires streamlit).

These exercise the real app with the example simulator configuration in a temporary data
directory. They have NOT been executed by the author (no Streamlit in the build sandbox); see
docs/VERIFICATION.md for the procedure and current status.
"""

import os
import tempfile
import unittest
from pathlib import Path

from helpers import requires_pydantic, requires_sqlalchemy, requires_streamlit

ROOT = Path(__file__).resolve().parents[2]


@requires_streamlit
@requires_pydantic
@requires_sqlalchemy
class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {
            k: os.environ.get(k)
            for k in ("TEXTILE_AUTOMATION_DATA_DIR", "TEXTILE_AUTOMATION_CONFIG")
        }
        os.environ["TEXTILE_AUTOMATION_DATA_DIR"] = self.tmp.name
        os.environ["TEXTILE_AUTOMATION_CONFIG"] = str(ROOT / "examples" / "simulator_config.toml")

    def tearDown(self):
        import gc
        import logging

        from textile_automation.runtime import AppRuntime

        # Stop any monitoring runtime the app created, then release the log file (Windows
        # cannot delete a file that is still open).
        for obj in gc.get_objects():
            if isinstance(obj, AppRuntime):
                obj.shutdown()
        root = logging.getLogger("textile_automation")
        for handler in list(root.handlers):
            if getattr(handler, "_tia_handler", False):
                handler.close()
                root.removeHandler(handler)
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        try:
            from streamlit.runtime import caching

            caching.cache_resource.clear()
        except Exception:  # noqa: BLE001
            pass
        self.tmp.cleanup()

    def test_overview_loads_without_exception_and_labels_simulation(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("SIMULATION MODE", text)
        self.assertEqual(at.sidebar.radio[0].value, "Overview")

    def test_navigation_opens_each_page(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        for page in ("Devices", "History", "Diagnostics", "Configuration"):
            at.sidebar.radio[0].set_value(page).run()
            self.assertFalse(at.exception, f"{page}: {at.exception}")
            self.assertIn(page, " ".join(h.value for h in at.header))


if __name__ == "__main__":
    unittest.main()

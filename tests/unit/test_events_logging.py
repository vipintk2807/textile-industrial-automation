import logging
import unittest

from helpers import FakeStorage
from textile_automation.clock import ManualClock
from textile_automation.domain.models import EventCategory as EC, Severity
from textile_automation.logging_setup import RateLimitFilter, RedactingFilter, configure_logging
from textile_automation.redaction import find_sensitive_keys, redact_mapping
from textile_automation.services.events import RING_SIZE, EventService


class EventTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.storage = FakeStorage()
        self.svc = EventService(self.storage, self.clock, dedup_window_s=60)

    def rec(self, msg="m", key="k", **kw):
        return self.svc.record(
            EC.ACQUISITION_FAILURE, Severity.WARNING, msg, device_id="d", dedup_key=key, **kw
        )

    def test_duplicates_suppressed_inside_window(self):
        self.assertIsNotNone(self.rec())
        for _ in range(5):
            self.assertIsNone(self.rec())
        self.assertEqual(len(self.storage.events), 1)
        self.assertEqual(self.svc.suppressed_total, 5)

    def test_next_event_after_window_reports_repeat_count(self):
        self.rec()
        for _ in range(3):
            self.rec()
        self.clock.advance(61)
        ev = self.rec()
        self.assertEqual(ev.details["suppressed_repeats"], 3)

    def test_different_keys_are_independent(self):
        self.assertIsNotNone(self.rec(key="a"))
        self.assertIsNotNone(self.rec(key="b"))

    def test_ring_buffer_is_bounded(self):
        for i in range(RING_SIZE + 50):
            self.rec(msg=str(i), key=i)
        self.assertEqual(len(self.svc.recent(10_000)), RING_SIZE)

    def test_storage_failure_is_counted_but_event_kept_in_memory(self):
        self.storage.fail = True
        ev = self.rec()
        self.assertIsNotNone(ev)
        self.assertEqual(self.svc.persist_failures, 1)
        self.assertEqual(len(self.svc.recent()), 1)

    def test_works_without_storage(self):
        svc = EventService(None, self.clock)
        self.assertIsNotNone(svc.record(EC.STARTUP, Severity.INFO, "x"))

    def test_secrets_redacted_in_details(self):
        ev = self.rec(details={"password": "hunter2", "nested": {"api_key": "abc"}, "ok": 1})
        self.assertEqual(ev.details["password"], "***")
        self.assertEqual(ev.details["nested"]["api_key"], "***")
        self.assertEqual(ev.details["ok"], 1)


class RedactionTests(unittest.TestCase):
    def test_redact_and_find(self):
        data = {"host": "h", "auth": {"Password": "x"}, "list": [{"token": "t"}]}
        self.assertEqual(redact_mapping(data)["auth"]["Password"], "***")
        self.assertEqual(sorted(find_sensitive_keys(data)), ["auth.Password", "list[0].token"])
        self.assertEqual(data["auth"]["Password"], "x")  # original untouched


class LoggingTests(unittest.TestCase):
    def rec(self, msg, *args):
        return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)

    def test_redacting_filter_masks_assignments(self):
        r = self.rec("connect password=hunter2 token: abc123 ok")
        RedactingFilter().filter(r)
        text = r.getMessage()
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abc123", text)
        self.assertIn("ok", text)

    def test_rate_limit_bounds_repeats(self):
        t = [0.0]
        f = RateLimitFilter(window_s=10, max_per_window=3, clock=lambda: t[0])
        allowed = sum(f.filter(self.rec("same")) for _ in range(20))
        self.assertEqual(allowed, 3)
        t[0] = 11
        self.assertTrue(f.filter(self.rec("same")))

    def test_configure_logging_writes_file_and_is_idempotent(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = configure_logging(Path(d) / "logs", "INFO", console=False)
            configure_logging(Path(d) / "logs", "INFO", console=False)
            lg = logging.getLogger("textile_automation.test")
            lg.info("hello password=abc", extra={"device_id": "dev-9"})
            handlers = [
                h
                for h in logging.getLogger("textile_automation").handlers
                if getattr(h, "_tia_handler", False)
            ]
            self.assertEqual(len(handlers), 1)
            root = logging.getLogger("textile_automation")
            for h in handlers:
                h.flush()
                h.close()
                root.removeHandler(h)
            text = path.read_text(encoding="utf-8")
            self.assertIn("dev-9", text)
            self.assertNotIn("abc", text)


if __name__ == "__main__":
    unittest.main()

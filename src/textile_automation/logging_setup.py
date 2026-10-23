"""Standard-library logging with redaction and bounded repetition."""

from __future__ import annotations

import logging
import re
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from textile_automation.redaction import REDACTED, SENSITIVE_KEY_PATTERN

_ASSIGNMENT = re.compile(
    rf"(?P<k>[\w.-]*{SENSITIVE_KEY_PATTERN.pattern}[\w.-]*)\s*[=:]\s*(?P<v>\S+)", re.IGNORECASE
)
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(device_id)s] %(message)s"


class RedactingFilter(logging.Filter):
    """Masks `password=...`-style fragments in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _ASSIGNMENT.sub(lambda m: f"{m.group('k')}={REDACTED}", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


class DeviceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "device_id"):
            record.device_id = "-"
        return True


class RateLimitFilter(logging.Filter):
    """Allow at most `max_per_window` identical messages per window; drop the rest.

    Keeps sustained communication failures from flooding the log file.
    """

    def __init__(self, window_s: float = 60.0, max_per_window: int = 5, clock=time.monotonic):
        super().__init__()
        self._window = window_s
        self._max = max_per_window
        self._clock = clock
        self._seen: dict[tuple[str, int, str], tuple[float, int]] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, str(record.msg))
        now = self._clock()
        with self._lock:
            start, count = self._seen.get(key, (now, 0))
            if now - start >= self._window:
                start, count = now, 0
            count += 1
            self._seen[key] = (start, count)
            if len(self._seen) > 1000:  # bounded memory
                self._seen.clear()
            return count <= self._max


def configure_logging(log_dir: Path, level: str = "INFO", console: bool = True) -> Path:
    """Install handlers on the 'textile_automation' logger. Idempotent."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "textile_automation.log"
    root = logging.getLogger("textile_automation")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        if getattr(handler, "_tia_handler", False):
            root.removeHandler(handler)
            handler.close()
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(DeviceIdFilter())
        handler.addFilter(RedactingFilter())
        handler.addFilter(RateLimitFilter())
        handler._tia_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.propagate = False
    return log_file

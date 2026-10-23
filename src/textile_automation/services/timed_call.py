"""Run a blocking call with a hard time limit, without ever blocking the caller forever.

The call runs in a daemon thread. If it does not finish in time the caller gets CallTimeout and
the thread is abandoned (daemon, so it cannot block interpreter exit). While an abandoned call is
still running the TimedCall reports `busy`, which lets the scheduler skip cycles instead of
issuing overlapping requests to the same device.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class CallTimeout(Exception):
    pass


class CallBusy(Exception):
    pass


class TimedCall:
    def __init__(self, name: str) -> None:
        self._name = name
        self._done: threading.Event | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        """True only while the last call has not finished.

        Deliberately based on the call's completion event, not Thread.is_alive(): a thread can
        stay alive for a moment after its function has returned, which would wrongly look busy.
        """
        done = self._done
        return done is not None and not done.is_set()

    def run(self, fn: Callable[[], Any], timeout_s: float) -> Any:
        with self._lock:
            if self.busy:
                raise CallBusy(f"{self._name}: previous call still running")
            box: dict[str, Any] = {}
            done = threading.Event()
            self._done = done

            def target() -> None:
                try:
                    box["value"] = fn()
                except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
                    box["error"] = exc
                finally:
                    done.set()

            threading.Thread(target=target, name=f"call-{self._name}", daemon=True).start()
        if not done.wait(timeout_s):
            raise CallTimeout(f"{self._name}: no result within {timeout_s:g} s")
        if "error" in box:
            raise box["error"]
        return box.get("value")

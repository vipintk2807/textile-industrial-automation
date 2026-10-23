"""Retry/backoff calculations. Pure functions so they can be tested exhaustively."""

from __future__ import annotations

from textile_automation.domain.models import RetryPolicy


def backoff_delay(policy: RetryPolicy, consecutive_failures: int) -> float:
    """Delay before the next attempt after `consecutive_failures` (>= 1) failures.

    delay = min(max_delay, initial_delay * multiplier ** (failures - 1)). No jitter, so the
    sequence is deterministic and testable.
    """
    if consecutive_failures < 1:
        raise ValueError("consecutive_failures must be >= 1")
    exponent = min(consecutive_failures - 1, 64)  # avoid float overflow
    return min(policy.max_delay_s, policy.initial_delay_s * policy.multiplier**exponent)


def retry_limit_reached(policy: RetryPolicy, consecutive_failures: int) -> bool:
    return consecutive_failures >= policy.max_consecutive_failures

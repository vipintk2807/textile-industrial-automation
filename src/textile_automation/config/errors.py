"""Configuration error type carrying every problem found (not just the first)."""

from __future__ import annotations

from collections.abc import Sequence

from textile_automation.domain.errors import SettingIssue


class ConfigError(Exception):
    def __init__(self, issues: Sequence[SettingIssue], source: str | None = None) -> None:
        self.issues = tuple(issues)
        self.source = source
        super().__init__(self._format())

    def _format(self) -> str:
        head = f"Invalid configuration{f' ({self.source})' if self.source else ''}:"
        return "\n".join([head, *(f"  - {i}" for i in self.issues)])

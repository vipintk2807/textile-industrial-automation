"""Startup sequence (app workflow 5.1) wired from real files: config -> logging -> storage."""

from __future__ import annotations

import logging
from pathlib import Path

from textile_automation.config.errors import ConfigError
from textile_automation.config.loader import load_config
from textile_automation.domain.errors import SettingIssue, StorageError
from textile_automation.logging_setup import configure_logging
from textile_automation.paths import RuntimePaths, resolve_config_path, resolve_log_level
from textile_automation.runtime import AppRuntime
from textile_automation.storage.repositories import SqlStorage

log = logging.getLogger("textile_automation.bootstrap")


def build_runtime(
    config_path: Path | None = None,
    data_dir: Path | None = None,
    *,
    console_logging: bool = True,
    search_root: Path | None = None,
) -> AppRuntime:
    """Create (but do not start) a runtime. Never raises for configuration/storage problems:
    it returns a FAILED runtime that carries actionable issues and acquires nothing."""
    path, is_example = resolve_config_path(config_path, cwd=search_root)
    paths = RuntimePaths.resolve(data_dir)
    configure_logging(paths.log_dir, resolve_log_level("INFO"), console=console_logging)
    if path is None:
        return AppRuntime.failed(
            [
                SettingIssue(
                    "",
                    "No configuration found. Copy config\\devices.example.toml to "
                    "config\\devices.toml (or set TEXTILE_AUTOMATION_CONFIG).",
                )
            ],
            paths=paths,
        )
    try:
        config = load_config(path)
    except ConfigError as exc:
        log.error("%s", exc)
        return AppRuntime.failed(
            exc.issues, config_path=str(path), using_example_config=is_example, paths=paths
        )
    paths = RuntimePaths.resolve(data_dir or config.data_dir, config.storage.database_path)
    configure_logging(paths.log_dir, resolve_log_level(config.log_level), console=console_logging)
    try:
        storage = SqlStorage.open(paths.database)
    except StorageError as exc:
        log.error("Storage initialisation failed: %s", exc)
        return AppRuntime.failed(
            [SettingIssue("storage", str(exc))],
            config_path=str(path),
            using_example_config=is_example,
            paths=paths,
        )
    return AppRuntime(
        config, storage, paths=paths, config_path=str(path), using_example_config=is_example
    )

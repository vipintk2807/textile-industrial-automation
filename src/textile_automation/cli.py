"""Command-line tools: validate-config, init-db, run-simulator (headless), backup, purge."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

from textile_automation import __version__


def _config_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", type=Path, help="path to a TOML configuration file")
    p.add_argument("--data-dir", type=Path, help="folder for the database and logs")


def cmd_validate(args: argparse.Namespace) -> int:
    from textile_automation.config.errors import ConfigError
    from textile_automation.config.loader import load_config
    from textile_automation.paths import resolve_config_path

    path, _ = resolve_config_path(args.config)
    if path is None:
        print("No configuration file found.", file=sys.stderr)
        return 2
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(
        f"OK: {path} - {len(config.devices)} device(s), "
        f"{sum(d.enabled for d in config.devices)} enabled."
    )
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    from textile_automation.domain.errors import StorageError
    from textile_automation.paths import RuntimePaths
    from textile_automation.storage.repositories import SqlStorage

    paths = RuntimePaths.resolve(args.data_dir, args.database)
    try:
        SqlStorage.open(paths.database).close()
    except StorageError as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 1
    print(f"Database ready: {paths.database}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from textile_automation.bootstrap import build_runtime
    from textile_automation.runtime import RuntimeStatus

    rt = build_runtime(args.config, args.data_dir)
    rt.start()
    if rt.status is not RuntimeStatus.RUNNING:
        for issue in rt.snapshot().startup_issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        rt.shutdown()
        return 1
    print("SIMULATION: all values below are synthetic. Press Ctrl+C to stop.")
    try:
        end = time.monotonic() + args.seconds if args.seconds else None
        while end is None or time.monotonic() < end:
            time.sleep(args.interval)
            for d in rt.snapshot().devices:
                vals = ", ".join(
                    f"{m.key}={m.value if m.value is not None else '-'}{(' ' + m.unit) if m.unit and m.value is not None else ''}"
                    f" [{m.quality.value}]"
                    for m in d.measurements
                )
                print(f"{d.device_id:<16} {d.comm_state.value:<12} {d.source_kind.value:<9} {vals}")
            print("-")
    except KeyboardInterrupt:
        pass
    report = rt.shutdown()
    return 0 if report is None or report.stopped_cleanly else 1


def cmd_backup(args: argparse.Namespace) -> int:
    from textile_automation.domain.errors import StorageError
    from textile_automation.paths import RuntimePaths
    from textile_automation.storage.database import backup_database

    paths = RuntimePaths.resolve(args.data_dir, args.database)
    try:
        dest = backup_database(paths.database, args.destination)
    except StorageError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Backup written: {dest}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    from textile_automation.clock import SystemClock
    from textile_automation.domain.errors import StorageError
    from textile_automation.paths import RuntimePaths
    from textile_automation.storage.repositories import SqlStorage

    paths = RuntimePaths.resolve(args.data_dir, args.database)
    cutoff = SystemClock().now() - timedelta(days=args.days)
    try:
        storage = SqlStorage.open(paths.database)
        try:
            m, e = storage.purge_before(cutoff)
        finally:
            storage.close()
    except StorageError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Removed {m} measurement(s) and {e} event(s) older than {args.days} day(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m textile_automation", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "validate-config", help="check a configuration file without starting anything"
    )
    _config_arg(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("init-db", help="create or upgrade the local database")
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--database", type=Path)
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("run-simulator", help="run monitoring in the terminal (no web interface)")
    _config_arg(p)
    p.add_argument(
        "--seconds", type=float, default=0, help="stop after N seconds (default: until Ctrl+C)"
    )
    p.add_argument("--interval", type=float, default=2.0, help="seconds between status prints")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("backup", help="write a consistent copy of the database")
    p.add_argument("destination", type=Path)
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--database", type=Path)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("purge", help="delete history older than N days (explicit, never automatic)")
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--database", type=Path)
    p.set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    if getattr(args, "days", 1) < 1:
        parser.error("--days must be >= 1")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

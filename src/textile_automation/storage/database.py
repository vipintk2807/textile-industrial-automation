"""Engine creation, migrations (Alembic), schema verification and online backup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import SQLAlchemyError

from textile_automation.domain.errors import StorageError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def make_engine(db_path: Path) -> Engine:
    """Create the SQLite engine. WAL lets the UI read while acquisition writes."""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(
            f"Cannot create the database folder '{db_path.parent}': {exc}. "
            "Choose a writable location (see [storage] database_path)."
        ) from exc
    engine = create_engine(
        URL.create("sqlite", database=str(db_path)),
        connect_args={"timeout": 10.0, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def migrate(engine: Engine) -> str:
    """Upgrade the database to the latest schema. Returns the resulting revision."""
    cfg = _alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:  # noqa: BLE001 - map every failure to an actionable StorageError
        raise StorageError(_explain(exc, engine)) from exc
    if current != head:
        raise StorageError(
            f"Database schema revision {current!r} does not match expected {head!r}."
        )
    return str(current)


def _explain(exc: Exception, engine: Engine) -> str:
    text = str(exc)
    path = engine.url.database
    if "Can't locate revision" in text:
        return (
            "The database was created by a newer or different version of this application "
            f"({path}). Use a matching version or a different database file."
        )
    if isinstance(exc, SQLAlchemyError) or "unable to open" in text.lower():
        return (
            f"Cannot open or initialise the database '{path}': {exc}. "
            "Check that the folder exists, is writable, and the file is not locked or corrupt."
        )
    return f"Database initialisation failed: {exc}"


def backup_database(db_path: Path, destination: Path) -> Path:
    """Consistent online backup using SQLite's backup API (safe while the app is running)."""
    if not db_path.is_file():
        raise StorageError(f"Database file not found: {db_path}")
    if destination.exists() and destination.is_dir():
        destination = destination / f"{db_path.stem}_backup.db"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(destination))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except (sqlite3.Error, OSError) as exc:
        raise StorageError(f"Backup failed: {exc}") from exc
    return destination

"""Alembic environment. Used programmatically (shared connection) and from the CLI."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from textile_automation.paths import DB_FILE_NAME, default_data_dir
from textile_automation.storage.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    shared = config.attributes.get("connection")
    if shared is not None:  # programmatic upgrade from textile_automation.storage.database
        _run(shared)
        return
    # CLI use (e.g. `alembic revision --autogenerate`): TEXTILE_AUTOMATION_DB or the default path.
    path = os.environ.get("TEXTILE_AUTOMATION_DB") or str(default_data_dir() / DB_FILE_NAME)
    engine = create_engine(URL.create("sqlite", database=path))
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; run online against a database file.")
run_migrations_online()

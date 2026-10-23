"""SqlStorage: the SQLAlchemy implementation of the StorageBackend port.

Rules: one short-lived Session per operation (never shared between threads); writes are
serialised by a lock and each batch is one transaction (all-or-nothing); every SQLAlchemy or OS
error becomes StorageError so callers can never mistake a failed write for a successful one;
queries are parameterised and use the indexes defined in models.py.
"""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, select, text
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from textile_automation.domain.errors import ErrorCategory, ErrorInfo, StorageError
from textile_automation.domain.models import (
    DataType,
    DeviceSpec,
    Event,
    EventCategory,
    MeasurementResult,
    Severity,
)
from textile_automation.domain.quality import Quality
from textile_automation.domain.states import SourceKind
from textile_automation.redaction import redact_mapping
from textile_automation.storage.database import make_engine, migrate
from textile_automation.storage.models import (
    DeviceRow,
    EventRow,
    MeasurementDefinitionRow,
    MeasurementRow,
)


def _guard[F: Callable[..., Any]](fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except StorageError:
            raise
        except (SQLAlchemyError, OSError, ValueError) as exc:
            raise StorageError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper  # type: ignore[return-value]


def _encode_value(value: object, data_type: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if data_type == "str":
        return None, str(value)
    if data_type == "bool":
        return (1.0 if value else 0.0), None
    return float(value), None  # type: ignore[arg-type]


def _decode_value(row: MeasurementRow) -> object:
    if row.value_type == "str":
        return row.value_text
    if row.value_number is None:
        return None
    if row.value_type == "bool":
        return bool(row.value_number)
    if row.value_type == "int":
        return int(row.value_number)
    return float(row.value_number)


class SqlStorage:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._write_lock = threading.Lock()

    @classmethod
    def open(cls, db_path: Path) -> SqlStorage:
        """Create/open the database and bring its schema to the latest revision."""
        engine = make_engine(db_path)
        try:
            migrate(engine)
        except StorageError:
            engine.dispose()
            raise
        return cls(engine)

    # -- writes ----------------------------------------------------------------------------
    @_guard
    def upsert_devices(self, devices: Sequence[DeviceSpec], source_kinds: dict[str, str]) -> None:
        now = datetime.now().astimezone()
        with self._write_lock, Session(self._engine) as session, session.begin():
            for spec in devices:
                config_json = json.dumps(
                    redact_mapping(
                        {
                            "poll_interval_s": spec.poll_interval_s,
                            "connect_timeout_s": spec.connect_timeout_s,
                            "response_timeout_s": spec.response_timeout_s,
                            "stale_after_intervals": spec.stale_after_intervals,
                            "adapter_settings": dict(spec.adapter_settings),
                        }
                    ),
                    sort_keys=True,
                    default=str,
                )
                row = session.get(DeviceRow, spec.device_id)
                if row is None:
                    row = DeviceRow(device_id=spec.device_id, created_at=now)
                    session.add(row)
                row.name, row.device_type = spec.name, spec.device_type
                row.adapter_type, row.enabled = spec.adapter_type, spec.enabled
                row.source_kind = source_kinds.get(spec.device_id, SourceKind.SIMULATOR.value)
                row.configuration, row.updated_at = config_json, now
                session.flush()
                session.execute(
                    delete(MeasurementDefinitionRow).where(
                        MeasurementDefinitionRow.device_id == spec.device_id
                    )
                )
                for m in spec.measurements:
                    session.add(
                        MeasurementDefinitionRow(
                            device_id=spec.device_id,
                            measurement_key=m.key,
                            name=m.name,
                            data_type=m.data_type.value,
                            unit=m.unit,
                            min_value=m.min_value,
                            max_value=m.max_value,
                        )
                    )

    @_guard
    def save_measurements(self, results: Sequence[MeasurementResult]) -> int:
        rows = []
        for r in results:
            if r.quality is not Quality.VALID:
                raise StorageError("Only validated (VALID) measurements may be stored.")
            data_type = _type_name(r.value)
            number, txt = _encode_value(r.value, data_type)
            rows.append(
                MeasurementRow(
                    device_id=r.device_id,
                    measurement_key=r.measurement_key,
                    value_type=data_type,
                    value_number=number,
                    value_text=txt,
                    unit=r.unit,
                    source_timestamp=r.source_timestamp,
                    received_at=r.received_at,
                    quality=r.quality.value,
                    source_kind=r.source_kind.value,
                    raw_value=None if r.raw_value is None else str(r.raw_value),
                    error_details=None if r.error is None else r.error.summary(),
                )
            )
        with self._write_lock, Session(self._engine) as session, session.begin():
            session.add_all(rows)  # one transaction: all rows or none
        return len(rows)

    @_guard
    def save_event(self, event: Event) -> None:
        row = EventRow(
            occurred_at=event.occurred_at,
            device_id=event.device_id,
            category=event.category.value,
            severity=event.severity.value,
            message=event.message,
            details=json.dumps(dict(event.details), sort_keys=True, default=str)
            if event.details
            else None,
        )
        with self._write_lock, Session(self._engine) as session, session.begin():
            session.add(row)

    @_guard
    def purge_before(self, cutoff: datetime) -> tuple[int, int]:
        with self._write_lock, Session(self._engine) as session, session.begin():
            m_result = cast(
                "CursorResult[Any]",
                session.execute(delete(MeasurementRow).where(MeasurementRow.received_at < cutoff)),
            )
            e_result = cast(
                "CursorResult[Any]",
                session.execute(delete(EventRow).where(EventRow.occurred_at < cutoff)),
            )
            m, e = m_result.rowcount, e_result.rowcount
        return int(m or 0), int(e or 0)

    # -- reads -----------------------------------------------------------------------------
    @_guard
    def health_check(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @_guard
    def latest_measurement(self, device_id: str, key: str) -> MeasurementResult | None:
        stmt = (
            select(MeasurementRow)
            .where(MeasurementRow.device_id == device_id, MeasurementRow.measurement_key == key)
            .order_by(MeasurementRow.received_at.desc(), MeasurementRow.id.desc())
            .limit(1)
        )
        with Session(self._engine) as session:
            row = session.execute(stmt).scalars().first()
            return None if row is None else _to_result(row)

    @_guard
    def query_measurements(
        self, device_id: str, key: str, start: datetime | None, end: datetime | None, limit: int
    ) -> list[MeasurementResult]:
        """Most recent `limit` rows in the interval, returned oldest-first."""
        stmt = select(MeasurementRow).where(
            MeasurementRow.device_id == device_id, MeasurementRow.measurement_key == key
        )
        if start is not None:
            stmt = stmt.where(MeasurementRow.received_at >= start)
        if end is not None:
            stmt = stmt.where(MeasurementRow.received_at <= end)
        stmt = stmt.order_by(MeasurementRow.received_at.desc(), MeasurementRow.id.desc()).limit(
            limit
        )
        with Session(self._engine) as session:
            rows = session.execute(stmt).scalars().all()
            return [_to_result(r) for r in reversed(rows)]

    @_guard
    def query_events(
        self,
        *,
        device_id: str | None = None,
        categories: Sequence[str] | None = None,
        severities: Sequence[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        stmt = select(EventRow)
        if device_id is not None:
            stmt = stmt.where(EventRow.device_id == device_id)
        if categories:
            stmt = stmt.where(EventRow.category.in_(list(categories)))
        if severities:
            stmt = stmt.where(EventRow.severity.in_(list(severities)))
        if start is not None:
            stmt = stmt.where(EventRow.occurred_at >= start)
        if end is not None:
            stmt = stmt.where(EventRow.occurred_at <= end)
        stmt = stmt.order_by(EventRow.occurred_at.desc(), EventRow.id.desc()).limit(limit)
        with Session(self._engine) as session:
            return [_to_event(r) for r in session.execute(stmt).scalars().all()]

    def close(self) -> None:
        self._engine.dispose()


def _type_name(value: object) -> str:
    if isinstance(value, bool):
        return DataType.BOOL.value
    if isinstance(value, int):
        return DataType.INT.value
    if isinstance(value, float):
        return DataType.FLOAT.value
    if isinstance(value, str):
        return DataType.STR.value
    raise StorageError(f"Unsupported value type for storage: {type(value).__name__}")


def _to_result(row: MeasurementRow) -> MeasurementResult:
    return MeasurementResult(
        device_id=row.device_id,
        measurement_key=row.measurement_key,
        value=_decode_value(row),
        unit=row.unit,
        received_at=row.received_at,
        source_kind=SourceKind(row.source_kind),
        quality=Quality(row.quality),
        source_timestamp=row.source_timestamp,
        raw_value=row.raw_value,
        error=None
        if row.error_details is None
        else ErrorInfo(ErrorCategory.UNEXPECTED_ERROR, row.error_details, "stored", row.device_id),
    )


def _to_event(row: EventRow) -> Event:
    return Event(
        occurred_at=row.occurred_at,
        category=EventCategory(row.category),
        severity=Severity(row.severity),
        message=row.message,
        device_id=row.device_id,
        details=json.loads(row.details) if row.details else {},
    )

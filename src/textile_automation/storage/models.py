"""SQLAlchemy 2.x ORM models. Timestamps are stored as UTC and returned timezone-aware."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Stores aware datetimes as naive UTC; always returns aware UTC. Rejects naive input."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime is not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class DeviceRow(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    device_type: Mapped[str] = mapped_column(String(100))
    adapter_type: Mapped[str] = mapped_column(String(32))
    source_kind: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    configuration: Mapped[str] = mapped_column(Text)  # JSON; secrets redacted
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MeasurementDefinitionRow(Base):
    __tablename__ = "measurement_definitions"
    __table_args__ = (
        UniqueConstraint("device_id", "measurement_key", name="uq_definition_device_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    measurement_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)


class MeasurementRow(Base):
    """Accepted (validated) measurements only. Rejected readings are stored as events."""

    __tablename__ = "measurements"
    __table_args__ = (
        Index("ix_measurements_device_key_time", "device_id", "measurement_key", "received_at"),
        Index("ix_measurements_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    measurement_key: Mapped[str] = mapped_column(String(64))
    value_type: Mapped[str] = mapped_column(String(8))  # float | int | bool | str
    value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    quality: Mapped[str] = mapped_column(String(16))
    source_kind: Mapped[str] = mapped_column(String(16))
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_occurred_at", "occurred_at"),
        Index("ix_events_device_time", "device_id", "occurred_at"),
        Index("ix_events_category_time", "category", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

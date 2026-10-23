"""Initial schema: devices, measurement definitions, measurements, events.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("device_type", sa.String(100), nullable=False),
        sa.Column("adapter_type", sa.String(32), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("configuration", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "measurement_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "device_id",
            sa.String(64),
            sa.ForeignKey("devices.device_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("measurement_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("data_type", sa.String(8), nullable=False),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.UniqueConstraint("device_id", "measurement_key", name="uq_definition_device_key"),
    )
    op.create_table(
        "measurements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "device_id",
            sa.String(64),
            sa.ForeignKey("devices.device_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("measurement_key", sa.String(64), nullable=False),
        sa.Column("value_type", sa.String(8), nullable=False),
        sa.Column("value_number", sa.Float(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_measurements_device_key_time",
        "measurements",
        ["device_id", "measurement_key", "received_at"],
    )
    op.create_index("ix_measurements_received_at", "measurements", ["received_at"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
    )
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_device_time", "events", ["device_id", "occurred_at"])
    op.create_index("ix_events_category_time", "events", ["category", "occurred_at"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("measurements")
    op.drop_table("measurement_definitions")
    op.drop_table("devices")

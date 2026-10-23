"""Pure presentation helpers: formatting, labels, escaping, history preparation.

Nothing here talks to devices, storage or Streamlit, so all of it is unit-tested. The UI shows
state and quality exactly as supplied by the services; it never recomputes them.
"""

from __future__ import annotations

import csv
import html
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

from textile_automation.domain.errors import ErrorInfo
from textile_automation.domain.models import MeasurementResult
from textile_automation.domain.quality import MeasurementView, Quality
from textile_automation.domain.states import CommunicationState, SourceKind
from textile_automation.services.device_state import DeviceSnapshot, OperatingView
from textile_automation.ui.theme import COMM_STYLES, QUALITY_STYLES, SOURCE_STYLES

NOT_AVAILABLE = "—"
MAX_TEXT = 200
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def safe_text(value: object, limit: int = MAX_TEXT) -> str:
    """Plain-text sanitising for device-supplied strings (control characters, length)."""
    text = _CONTROL.sub("", str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def esc(value: object) -> str:
    """HTML-escape device/user supplied text. Device strings are never rendered as markup."""
    return html.escape(safe_text(value), quote=True)


def _fmt_float(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e15 or abs(v) < 1e-6:
        return f"{v:.6g}"
    return f"{v:,.6f}".rstrip("0").rstrip(".")


def format_value(value: object, unit: str | None) -> str:
    """Value with its configured unit. None is 'Not available', never zero."""
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int):
        text = f"{value:,}"
    elif isinstance(value, float):
        text = _fmt_float(value)
    else:
        text = safe_text(value)
    return f"{text} {unit}" if unit and not isinstance(value, str | bool) else text


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 2:
        return "just now"
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min {s % 60} s ago"
    if s < 86400:
        return f"{s // 3600} h {(s % 3600) // 60} min ago"
    return f"{s // 86400} d {(s % 86400) // 3600} h ago"


def format_timestamp(ts: datetime | None, tz: tzinfo | None = None) -> str:
    if ts is None:
        return NOT_AVAILABLE
    local = ts.astimezone(tz) if tz is not None else ts.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S")


def badge(css_class: str, symbol: str, label: str, aria: str) -> str:
    return (
        f'<span class="tia-badge {css_class}" role="status" aria-label="{esc(aria)}">'
        f"{esc(symbol)} {esc(label)}</span>"
    )


def comm_badge(state: CommunicationState) -> str:
    style = COMM_STYLES[state]
    return badge(style.css_class, style.symbol, state.value, f"Communication state: {state.value}")


def quality_badge(quality: Quality) -> str:
    style = QUALITY_STYLES[quality]
    return badge(style.css_class, style.symbol, quality.value, f"Data quality: {quality.value}")


def source_badge(kind: SourceKind) -> str:
    style = SOURCE_STYLES[kind]
    label = "SIMULATED" if kind is SourceKind.SIMULATOR else "PHYSICAL"
    return badge(style.css_class, style.symbol, label, f"Data source: {label}")


def comm_label(state: CommunicationState) -> str:
    return f"{COMM_STYLES[state].symbol} {state.value}"


def quality_label(quality: Quality) -> str:
    return f"{QUALITY_STYLES[quality].symbol} {quality.value}"


def source_label(kind: SourceKind) -> str:
    return "SIMULATED" if kind is SourceKind.SIMULATOR else "PHYSICAL"


def describe_failure(snap: DeviceSnapshot) -> str | None:
    """'Read timeout — Device sim-01 — Retrying'-style summary (theme spec 8.4)."""
    err: ErrorInfo | None = snap.last_error
    if err is None or snap.comm_state in (
        CommunicationState.CONNECTED,
        CommunicationState.DISABLED,
    ):
        return None
    if snap.comm_state is CommunicationState.ERROR:
        action = (
            "Retry limit reached — operator action required"
            if err.retryable
            else "Not retryable — operator action required"
        )
    elif snap.comm_state in (
        CommunicationState.DEGRADED,
        CommunicationState.RECOVERING,
        CommunicationState.DISCONNECTED,
        CommunicationState.CONNECTING,
    ):
        action = (
            f"Retrying (failure {snap.consecutive_failures} of {snap.retry_limit})"
            if snap.consecutive_failures
            else "Monitoring"
        )
    else:
        action = "Monitoring"
    return f"{err.label} — Device {safe_text(snap.device_id)} — {action}"


def operating_text(view: OperatingView) -> str:
    if view.state is None:
        return "Unknown" if view.reported_at is not None else "Not reported"
    return view.state.value


def measurement_rows(snap: DeviceSnapshot) -> list[dict[str, str]]:
    rows = []
    for m in snap.measurements:
        rows.append(
            {
                "Measurement": safe_text(m.name),
                "Last accepted value": format_value(m.value, m.unit),
                "Quality": quality_label(m.quality),
                "Age": format_age(m.age_s),
                "Received (local)": format_timestamp(m.last_valid_received_at),
                "Source timestamp": format_timestamp(m.source_timestamp),
                "Source": source_label(snap.source_kind),
                "Note": safe_text(m.note or ""),
            }
        )
    return rows


def device_table_rows(devices: Sequence[DeviceSnapshot]) -> list[dict[str, str]]:
    rows = []
    for d in devices:
        counts = ", ".join(f"{k} {v}" for k, v in sorted(d.quality_counts.items())) or NOT_AVAILABLE
        rows.append(
            {
                "Device": f"{safe_text(d.name)} ({safe_text(d.device_id)})",
                "Source": f"{source_label(d.source_kind)} / {d.adapter_type}",
                "Communication": comm_label(d.comm_state),
                "Machine state": operating_text(d.operating),
                "Data quality": counts,
                "Last successful acquisition": format_timestamp(d.last_success_at),
                "Latest error": describe_failure(d)
                or (
                    d.last_error.summary()
                    if d.last_error and d.comm_state is not CommunicationState.CONNECTED
                    else NOT_AVAILABLE
                ),
            }
        )
    return rows


def needs_attention(d: DeviceSnapshot) -> bool:
    if d.comm_state in (
        CommunicationState.DEGRADED,
        CommunicationState.RECOVERING,
        CommunicationState.ERROR,
        CommunicationState.DISCONNECTED,
    ):
        return True
    return any(
        m.quality in (Quality.INVALID, Quality.MISSING, Quality.STALE) for m in d.measurements
    )


# ------------------------------------------------------------------------------ history
@dataclass(frozen=True)
class HistoryPoint:
    received_at: datetime
    value: float
    source_kind: str
    segment: int


def segment_by_gaps(
    results: Sequence[MeasurementResult], max_gap_s: float
) -> tuple[list[HistoryPoint], list[tuple[datetime, datetime]]]:
    """Numeric points with a segment id that increases after every gap (so charts break the
    line instead of interpolating across missing data), plus the list of gaps."""
    points: list[HistoryPoint] = []
    gaps: list[tuple[datetime, datetime]] = []
    segment = 0
    previous: MeasurementResult | None = None
    for r in results:
        if isinstance(r.value, bool) or not isinstance(r.value, int | float):
            continue
        if (
            previous is not None
            and (r.received_at - previous.received_at).total_seconds() > max_gap_s
        ):
            segment += 1
            gaps.append((previous.received_at, r.received_at))
        points.append(HistoryPoint(r.received_at, float(r.value), r.source_kind.value, segment))
        previous = r
    return points, gaps


_CSV_DANGER = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(_CSV_DANGER):
        return "'" + value  # neutralise spreadsheet formula injection from device-supplied text
    return value


def to_csv(results: Sequence[MeasurementResult]) -> str:
    """CSV export that keeps source identity, units and both timestamps."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "device_id",
            "measurement_key",
            "value",
            "unit",
            "source_timestamp_utc",
            "received_at_utc",
            "source_kind",
            "quality",
        ]
    )
    for r in results:
        writer.writerow(
            [
                _csv_cell(r.device_id),
                _csv_cell(r.measurement_key),
                "" if r.value is None else _csv_cell(r.value),
                _csv_cell(r.unit or ""),
                r.source_timestamp.isoformat() if r.source_timestamp else "",
                r.received_at.isoformat(),
                r.source_kind.value,
                r.quality.value,
            ]
        )
    return buf.getvalue()


def view_summary(view: MeasurementView) -> str:
    """One-line description used by cards: the value is always labelled with its quality."""
    return f"{format_value(view.value, view.unit)} — {view.quality.value}"


# ------------------------------------------------------------------------------ banners
@dataclass(frozen=True)
class BannerSpec:
    kind: str  # "info" | "warn" | "danger" | "sim"
    text: str


def banner_specs(snapshot: object) -> list[BannerSpec]:
    """Page-level notices derived from the runtime snapshot (never from the UI's own guesses)."""
    from textile_automation.services.persistence import PersistenceMode

    banners: list[BannerSpec] = []
    if getattr(snapshot, "any_simulated", False):
        banners.append(
            BannerSpec(
                "sim",
                "SIMULATION MODE — measurements from simulated devices are synthetic, produced by "
                "the built-in simulator. They are not data from any real machine.",
            )
        )
    if getattr(snapshot, "using_example_config", False):
        banners.append(
            BannerSpec(
                "info",
                "Using the example configuration (config\\devices.example.toml). Copy it to "
                "config\\devices.toml to make your own settings.",
            )
        )
    persistence = getattr(snapshot, "persistence", None)
    if persistence is not None and persistence.mode is PersistenceMode.RESTRICTED:
        banners.append(
            BannerSpec(
                "danger",
                f"RESTRICTED MODE — history is NOT being saved ({persistence.last_error}). "
                f"{persistence.dropped_measurements} measurement(s) have been dropped. "
                "Live values below are still updating.",
            )
        )
    failures = getattr(snapshot, "event_persist_failures", 0)
    if failures:
        banners.append(
            BannerSpec("warn", f"{failures} event(s) could not be stored in the database.")
        )
    return banners

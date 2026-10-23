"""Diagnostics: runtime/persistence status, active faults and filterable event history."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from textile_automation.domain.errors import StorageError
from textile_automation.domain.models import EventCategory, Severity
from textile_automation.runtime import AppRuntime
from textile_automation.ui import presenters as P
from textile_automation.ui.components import banners, html, refreshing, table

HOURS = {
    "Last hour": 1,
    "Last 6 hours": 6,
    "Last 24 hours": 24,
    "Last 7 days": 168,
    "All stored": 0,
}


def render(runtime: AppRuntime) -> None:
    st.header("Diagnostics")

    def status_body() -> None:
        snap = runtime.snapshot()
        banners(snap)
        st.write(f"**Monitoring status:** {snap.status.value}")
        for issue in snap.startup_issues:
            st.error(str(issue))
        p = snap.persistence
        if p is not None:
            st.write(
                f"**Persistence:** {p.mode.value} · writes ok {p.writes_ok} · failed {p.writes_failed} "
                f"· dropped measurements {p.dropped_measurements}"
            )
        st.subheader("Active faults")
        faults = [d for d in snap.devices if P.describe_failure(d)]
        if faults:
            for d in faults:
                html(f"{P.comm_badge(d.comm_state)} {P.esc(P.describe_failure(d))}")
        else:
            st.success("No devices have an active communication fault.")
        st.subheader("Rejected, missing and stale measurements")
        problems = [
            {
                "Device": d.device_id,
                "Measurement": m.name,
                "Quality": P.quality_label(m.quality),
                "Detail": P.safe_text(m.note or ""),
            }
            for d in snap.devices
            for m in d.measurements
            if m.quality.value != "VALID"
        ]
        table(problems, "All measurements are currently VALID.")

    refreshing(status_body, runtime.snapshot().ui_refresh_seconds)

    st.subheader("Event history")
    snap = runtime.snapshot()
    c1, c2, c3, c4 = st.columns(4)
    device = c1.selectbox("Device", ["All", *[d.device_id for d in snap.devices]], key="ev_device")
    cats = c2.multiselect("Category", [c.value for c in EventCategory], key="ev_cat")
    sevs = c3.multiselect("Severity", [s.value for s in Severity], key="ev_sev")
    window = c4.selectbox("Time range", list(HOURS), index=2, key="ev_range")
    start = None if not HOURS[window] else runtime.clock.now() - timedelta(hours=HOURS[window])
    st.button("Refresh events")
    try:
        events = runtime.stored_events(
            device_id=None if device == "All" else device,
            categories=cats or None,
            severities=sevs or None,
            start=start,
            limit=300,
        )
        origin = "stored in the database"
    except StorageError as exc:
        st.error(
            f"Stored event history is unavailable ({exc}). Showing this session's in-memory events."
        )
        events = [
            e
            for e in runtime.events.recent(300)
            if (device == "All" or e.device_id == device)
            and (not cats or e.category.value in cats)
            and (not sevs or e.severity.value in sevs)
        ]
        origin = "in memory (this session only)"
    st.caption(
        f"{len(events)} event(s), {origin}. Repeated identical events are summarised, not repeated."
    )
    table(
        [
            {
                "Time (local)": P.format_timestamp(e.occurred_at),
                "Severity": e.severity.value,
                "Category": e.category.value,
                "Device": e.device_id or "—",
                "Message": P.safe_text(e.message),
                "Repeats suppressed": str(e.details.get("suppressed_repeats", "")),
            }
            for e in events
        ],
        "No events match these filters.",
    )

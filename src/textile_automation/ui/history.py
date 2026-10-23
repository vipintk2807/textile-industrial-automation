"""History: stored measurements over a bounded time range, with gap-aware chart and CSV export."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from textile_automation.domain.errors import StorageError
from textile_automation.runtime import AppRuntime
from textile_automation.ui import presenters as P
from textile_automation.ui.components import banners, table

RANGES = {
    "Last 15 minutes": 15 * 60,
    "Last hour": 3600,
    "Last 6 hours": 6 * 3600,
    "Last 24 hours": 24 * 3600,
    "Last 7 days": 7 * 24 * 3600,
}
MAX_POINTS = 5000
MAX_TABLE_ROWS = 500


def render(runtime: AppRuntime) -> None:
    st.header("History")
    snap = runtime.snapshot()
    banners(snap)
    devices = [d for d in snap.devices if d.enabled]
    if not devices:
        st.info("No enabled devices. History is available for devices that have been monitored.")
        return
    c1, c2, c3 = st.columns(3)
    device_id = c1.selectbox("Device", [d.device_id for d in devices], key="hist_device")
    device = next(d for d in devices if d.device_id == device_id)
    key = c2.selectbox("Measurement", [m.key for m in device.measurements], key="hist_key")
    label = c3.selectbox("Time range", list(RANGES), index=1, key="hist_range")
    spec = next(m for m in device.measurements if m.key == key)
    st.button("Refresh history")  # a rerun re-queries stored data; it never polls devices
    end = runtime.clock.now()
    start = end - timedelta(seconds=RANGES[label])
    try:
        results = runtime.history(device_id, key, start, end, MAX_POINTS)
    except StorageError as exc:
        st.error(f"History is unavailable: {exc}")
        return
    if not results:
        st.info("No accepted measurements are stored for this device and time range.")
        return
    if len(results) >= MAX_POINTS:
        st.warning(f"Showing only the most recent {MAX_POINTS} stored points of this range.")
    kinds = sorted({r.source_kind.value for r in results})
    st.caption(
        f"{len(results)} stored point(s) · source: {', '.join(kinds)} · "
        "only accepted (VALID) measurements are stored; rejected readings appear in Diagnostics."
    )
    max_gap = device.freshness_threshold_s
    points, gaps = P.segment_by_gaps(results, max_gap)
    if points:
        import altair as alt
        import pandas as pd

        frame = pd.DataFrame(
            [
                {
                    "received": p.received_at.astimezone().replace(tzinfo=None),
                    "value": p.value,
                    "source": "SIMULATED" if p.source_kind == "SIMULATOR" else "PHYSICAL",
                    "segment": str(p.segment),
                }
                for p in points
            ]
        )
        chart = (
            alt.Chart(frame)
            .mark_line(point=True)
            .encode(
                x=alt.X("received:T", title="Received (local time)"),
                y=alt.Y(
                    "value:Q",
                    title=f"{spec.name} ({spec.unit or 'unit not configured'})",
                    scale=alt.Scale(zero=False),
                ),
                color=alt.Color("source:N", title="Source"),
                detail="segment:N",
                tooltip=["received:T", "value:Q", "source:N"],
            )
            .properties(height=320)
        )
        st.altair_chart(chart)
        st.caption("The line is broken wherever data is missing; gaps are never interpolated.")
    else:
        st.info("This measurement is not numeric, so no chart is drawn. See the table below.")
    if gaps:
        st.warning(f"{len(gaps)} gap(s) with no accepted measurements (longer than {max_gap:g} s).")
        table(
            [
                {
                    "Gap starts after (local)": P.format_timestamp(a),
                    "Next measurement (local)": P.format_timestamp(b),
                    "Duration": f"{(b - a).total_seconds():.1f} s",
                }
                for a, b in gaps[:50]
            ],
            "",
        )
    rows = [
        {
            "Received (local)": P.format_timestamp(r.received_at),
            "Source timestamp": P.format_timestamp(r.source_timestamp),
            "Value": P.format_value(r.value, r.unit),
            "Quality": P.quality_label(r.quality),
            "Source": P.source_label(r.source_kind),
        }
        for r in reversed(results[-MAX_TABLE_ROWS:])
    ]
    st.subheader("Stored records (newest first)")
    table(rows, "")
    st.download_button(
        "Download this range as CSV (source identity included)",
        P.to_csv(results),
        file_name=f"{device_id}_{key}.csv",
        mime="text/csv",
    )

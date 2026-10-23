"""Overview: counts, device summary cards, latest measurements."""

from __future__ import annotations

import streamlit as st

from textile_automation.domain.states import CommunicationState
from textile_automation.runtime import AppRuntime
from textile_automation.services.device_state import DeviceSnapshot
from textile_automation.ui import presenters as P
from textile_automation.ui.components import banners, html, refreshing, table


def _card(d: DeviceSnapshot) -> str:
    problem = P.describe_failure(d)
    return (
        '<div class="tia-card">'
        f"<h4>{P.esc(d.name)}</h4>"
        f'<div class="tia-meta tia-mono">{P.esc(d.device_id)}</div>'
        f"{P.comm_badge(d.comm_state)}{P.source_badge(d.source_kind)}"
        f'<div class="tia-meta">Machine state: {P.esc(P.operating_text(d.operating))} '
        "(as reported by the source; independent of communication)</div>"
        f'<div class="tia-meta">Measurements: {len(d.measurements)} &middot; '
        f"last successful acquisition: {P.esc(P.format_timestamp(d.last_success_at))}</div>"
        + (f'<div class="tia-meta"><strong>{P.esc(problem)}</strong></div>' if problem else "")
        + "</div>"
    )


def render(runtime: AppRuntime) -> None:
    st.header("Overview")

    def body() -> None:
        snap = runtime.snapshot()
        banners(snap)
        devices = snap.devices
        if not devices:
            st.info("No devices are configured or monitoring has not started.")
            return
        enabled = [d for d in devices if d.enabled]
        connected = [d for d in enabled if d.comm_state is CommunicationState.CONNECTED]
        attention = [d for d in devices if P.needs_attention(d) and d.enabled]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Devices configured", len(devices))
        c2.metric("Enabled", len(enabled))
        c3.metric("Communicating (CONNECTED)", len(connected))
        c4.metric("Need attention", len(attention))
        st.caption(
            "CONNECTED means the monitoring link works and the latest read was fully "
            "accepted. It does not show that a machine is running or operating safely."
        )
        st.subheader("Devices")
        cols = st.columns(3)
        for i, d in enumerate(devices):
            with cols[i % 3]:
                html(_card(d))
        st.subheader("Latest measurements")
        rows = []
        for d in devices:
            for row in P.measurement_rows(d):
                rows.append({"Device": d.device_id, **row})
        table(rows, "No measurements to show yet.")

    refreshing(body, runtime.snapshot().ui_refresh_seconds)

"""Devices: table, selected-device details, retry action, simulator-only fault controls."""

from __future__ import annotations

import streamlit as st

from textile_automation.domain.errors import OperationNotPermitted
from textile_automation.domain.states import CommunicationState, SourceKind
from textile_automation.runtime import AppRuntime
from textile_automation.services.device_state import DeviceSnapshot
from textile_automation.ui import presenters as P
from textile_automation.ui.components import banners, html, refreshing, table

SIM_FAULTS = {
    "Read timeout": "read_timeout",
    "Link drop (forces a reconnect)": "link_drop",
    "Connection refused": "connect_failure",
    "Missing measurement": "missing",
    "Invalid value (NaN)": "invalid_value",
}


def _measurement_card(d: DeviceSnapshot, m) -> str:  # type: ignore[no-untyped-def]
    dim = "" if m.quality.value == "VALID" else " tia-dim"
    unit_note = ""
    if m.unit is None and isinstance(m.value, int | float) and not isinstance(m.value, bool):
        unit_note = '<div class="tia-meta">unit not configured</div>'
    return (
        '<div class="tia-card">'
        f"<h4>{P.esc(m.name)}</h4>"
        f'<div class="tia-value{dim}">{P.esc(P.format_value(m.value, m.unit))}</div>'
        f"{unit_note}{P.quality_badge(m.quality)}{P.source_badge(d.source_kind)}"
        f'<div class="tia-meta">Last accepted: {P.esc(P.format_age(m.age_s))} '
        f"({P.esc(P.format_timestamp(m.last_valid_received_at))})</div>"
        + (f'<div class="tia-meta">{P.esc(m.note)}</div>' if m.note else "")
        + "</div>"
    )


def _details(runtime: AppRuntime, d: DeviceSnapshot) -> None:
    st.subheader(P.safe_text(d.name))
    html(f"{P.comm_badge(d.comm_state)}{P.source_badge(d.source_kind)}")
    st.write(f"**Machine operating state:** {P.operating_text(d.operating)} — {d.operating.note}")
    st.write(
        f"**Communication since:** {P.format_timestamp(d.state_since)} · "
        f"**Last successful acquisition:** {P.format_timestamp(d.last_success_at)}"
    )
    problem = P.describe_failure(d)
    if problem:
        st.error(problem) if d.comm_state is CommunicationState.ERROR else st.warning(problem)
    if d.comm_state is CommunicationState.ERROR:
        if d.last_error is not None and d.last_error.retryable:
            if st.button("Retry this device", key=f"retry-{d.device_id}"):
                st.success("Retry requested.") if runtime.retry_device(d.device_id) else st.info(
                    "Nothing to retry."
                )
        else:
            st.info(
                "This error is not retryable automatically. Fix the configuration and "
                "restart the application."
            )
    st.markdown("**Measurements** (quality is shown separately from communication state)")
    cols = st.columns(3)
    for i, m in enumerate(d.measurements):
        with cols[i % 3]:
            html(_measurement_card(d, m))
    table(P.measurement_rows(d), "No measurements configured.")
    st.caption(
        f"Cycles ok {d.cycles_ok} · failed {d.cycles_failed} · skipped {d.cycles_skipped} · "
        f"accepted {d.accepted_total} · rejected {d.rejected_total} · missing {d.missing_total}"
    )
    st.markdown("**Recent events for this device**")
    events = [e for e in runtime.events.recent(300) if e.device_id == d.device_id][:20]
    table(
        [
            {
                "Time": P.format_timestamp(e.occurred_at),
                "Severity": e.severity.value,
                "Category": e.category.value,
                "Message": P.safe_text(e.message),
            }
            for e in events
        ],
        "No events recorded for this device in this session.",
    )


def _simulator_controls(runtime: AppRuntime, d: DeviceSnapshot) -> None:
    with st.expander("Simulator test controls (synthetic faults only)"):
        st.caption(
            "Schedules a synthetic fault in the SIMULATOR for the next read(s). It cannot "
            "affect real equipment: fault injection is refused for non-simulated devices."
        )
        label = st.selectbox("Fault", list(SIM_FAULTS), key=f"fault-kind-{d.device_id}")
        kind = SIM_FAULTS[label]
        duration = st.number_input("Number of attempts", 1, 100, 3, key=f"fault-dur-{d.device_id}")
        key = None
        if kind in ("missing", "invalid_value"):
            key = st.selectbox(
                "Measurement", [m.key for m in d.measurements], key=f"fault-key-{d.device_id}"
            )
        if st.button("Inject synthetic fault", key=f"inject-{d.device_id}"):
            fault: dict[str, object] = {"kind": kind, "duration": int(duration)}
            if key:
                fault["key"] = key
            if kind == "invalid_value":
                fault["mode"] = "nan"
            try:
                runtime.inject_simulator_fault(d.device_id, **fault)
                st.success("Synthetic fault scheduled.")
            except (OperationNotPermitted, ValueError) as exc:
                st.error(str(exc))


def render(runtime: AppRuntime) -> None:
    st.header("Devices")

    def body() -> None:
        snap = runtime.snapshot()
        banners(snap)
        if not snap.devices:
            st.info("No devices are configured or monitoring has not started.")
            return
        table(P.device_table_rows(snap.devices), "No devices.")
        ids = [d.device_id for d in snap.devices]
        chosen = st.selectbox(
            "Select a device for details",
            ids,
            key="selected_device",
            format_func=lambda i: (
                f"{P.safe_text(next(d.name for d in snap.devices if d.device_id == i))} ({i})"
            ),
        )
        d = next(x for x in snap.devices if x.device_id == chosen)
        _details(runtime, d)
        if d.source_kind is SourceKind.SIMULATOR and d.enabled:
            _simulator_controls(runtime, d)

    refreshing(body, runtime.snapshot().ui_refresh_seconds)

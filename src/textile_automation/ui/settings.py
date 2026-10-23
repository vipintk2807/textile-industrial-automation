"""Configuration (read-only): validation status, loaded devices, data locations, scope."""

from __future__ import annotations

import streamlit as st

from textile_automation.redaction import redact_mapping
from textile_automation.runtime import AppRuntime, RuntimeStatus
from textile_automation.ui import presenters as P
from textile_automation.ui.components import banners, table


def render(runtime: AppRuntime) -> None:
    st.header("Configuration")
    snap = runtime.snapshot()
    banners(snap)
    st.write(f"**Configuration file:** `{snap.config_path or 'none'}`")
    if snap.startup_issues:
        st.error("The configuration or storage could not be used. Nothing is being acquired.")
        for issue in snap.startup_issues:
            st.write(f"- {P.safe_text(issue)}")
    elif snap.status is RuntimeStatus.RUNNING:
        st.success("Configuration validated before any device communication started.")
    rows = []
    for spec in runtime.config.devices:
        rows.append(
            {
                "Device": f"{P.safe_text(spec.name)} ({spec.device_id})",
                "Adapter": spec.adapter_type,
                "Enabled": "yes" if spec.enabled else "no (never polled)",
                "Poll interval": f"{spec.poll_interval_s:g} s",
                "Timeouts (connect/response)": f"{spec.connect_timeout_s:g} s / {spec.response_timeout_s:g} s",
                "Stale after": f"{spec.poll_interval_s * spec.stale_after_intervals:g} s",
                "Retry (initial/max/limit)": f"{spec.retry.initial_delay_s:g} s / {spec.retry.max_delay_s:g} s / "
                f"{spec.retry.max_consecutive_failures}",
                "Adapter settings": P.safe_text(redact_mapping(dict(spec.adapter_settings)), 160),
            }
        )
    table(rows, "No devices loaded.")
    st.subheader("Data locations")
    st.write(f"Database: `{snap.database_path or 'n/a'}`  \nLogs: `{snap.log_dir or 'n/a'}`")
    st.write(
        f"History retention: {f'{snap.retention_days} days' if snap.retention_days else 'keep everything (no automatic deletion)'}"
    )
    st.subheader("Scope and safety")
    st.markdown(
        "- This application is **read-only monitoring**. It has no machine-control, setpoint or "
        "register-write function.\n"
        "- Only the **simulator** adapter is available in this release. No physical protocol has "
        "been verified against real equipment.\n"
        "- It is not a certified industrial HMI and does not replace machine safety systems.\n"
        "- Changes to the configuration file take effect after restarting the application."
    )
